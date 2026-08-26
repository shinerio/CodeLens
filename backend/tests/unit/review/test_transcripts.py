import logging
from pathlib import Path

import pytest

from codelens.review.infrastructure.transcripts import (
    ExecutionTranscriptStore,
    WorkerTranscriptStore,
)


async def test_transcript_redacts_credentials_and_preserves_entry_order(tmp_path: Path) -> None:
    store = ExecutionTranscriptStore(tmp_path)
    task_id = "review_" + "a" * 32

    await store.append(task_id, "lifecycle", "Review execution started")
    await store.append(
        task_id,
        "prompt",
        "Authorization: Bearer secret-value\napi_key=another-secret\nReview this change.",
        metadata={"agent": "correctness:v2"},
    )

    entries = await store.list(task_id)

    assert [entry.sequence for entry in entries] == [1, 2]
    assert entries[1].redacted
    assert "secret-value" not in entries[1].content
    assert "another-secret" not in entries[1].content
    assert entries[1].metadata == {"agent": "correctness:v2"}


async def test_transcript_append_many_reads_and_writes_one_complete_batch(tmp_path: Path) -> None:
    store = ExecutionTranscriptStore(tmp_path)
    task_id = "review_" + "e" * 32

    await store.append_many(
        task_id,
        (
            ("model_started", "", {"agent": "correctness:v2"}),
            ("model_reasoning_delta", "Checking change map", {"agent": "correctness:v2"}),
            ("model_output_delta", "No defect found", {"agent": "correctness:v2"}),
        ),
    )

    entries = await store.list(task_id)

    assert [entry.sequence for entry in entries] == [1, 2, 3]
    assert [entry.content for entry in entries] == ["", "Checking change map", "No defect found"]


async def test_transcript_returns_empty_entries_for_a_review_without_execution(
    tmp_path: Path,
) -> None:
    store = ExecutionTranscriptStore(tmp_path)

    assert await store.list("review_" + "b" * 32) == ()


async def test_transcript_keeps_complete_stream_chunks_without_truncation(tmp_path: Path) -> None:
    """Streaming console payloads remain lossless when restored after a reconnect."""

    store = ExecutionTranscriptStore(tmp_path)
    content = "model-token " * 30_000

    await store.append(
        "review_" + "c" * 32,
        "model_output_delta",
        content,
        metadata={"agent": "correctness:v2", "message_id": "message-1"},
    )

    (entry,) = await store.list("review_" + "c" * 32)

    assert entry.content == content
    assert not entry.truncated


async def test_transcript_append_ignores_a_stale_temporary_file(tmp_path: Path) -> None:
    """A previous interrupted write cannot prevent a Worker from resuming a Review."""

    store = ExecutionTranscriptStore(tmp_path)
    task_id = "review_" + "d" * 32
    (tmp_path / f"{task_id}.tmp").write_text("partial", encoding="utf-8")

    await store.append(task_id, "lifecycle", "Review execution started")

    (entry,) = await store.list(task_id)
    assert entry.content == "Review execution started"


async def test_worker_transcript_keeps_entries_in_memory_until_finalize(tmp_path: Path) -> None:
    """Worker transcripts remain in memory during execution and persist on finalize."""

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    task_id = "review_" + "g" * 32

    await worker_store.append(task_id, "model_output_delta", "visible while running")

    assert [entry.content for entry in await worker_store.list(task_id)] == [
        "visible while running"
    ]
    assert await durable.list(task_id) == ()

    await worker_store.finalize(task_id)

    assert await worker_store.list(task_id) == ()
    assert [entry.content for entry in await durable.list(task_id)] == ["visible while running"]


async def test_worker_transcript_assigns_contiguous_sequences_across_batches(
    tmp_path: Path,
) -> None:
    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    task_id = "review_" + "h" * 32

    await worker_store.append_many(
        task_id,
        (
            ("model_output_delta", "first", None),
            ("tool_call", "second", None),
            ("tool_result", "third", None),
        ),
    )
    await worker_store.append_many(
        task_id,
        (
            ("tool_call", "fourth", None),
            ("tool_result", "fifth", None),
        ),
    )

    entries = await worker_store.list(task_id)

    assert [entry.sequence for entry in entries] == [1, 2, 3, 4, 5]


async def test_worker_transcript_appends_each_delta_without_merging(
    tmp_path: Path,
) -> None:
    """Each delta is stored as a separate entry; the backend emits full text per call."""

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    task_id = "review_" + "i" * 32

    await worker_store.append_many(
        task_id,
        (
            (
                "model_output_delta",
                "Security ",
                {"agent": "security:v2", "message_id": "provider-message"},
            ),
            (
                "model_output_delta",
                "Correctness ",
                {"agent": "correctness:v2", "message_id": "provider-message"},
            ),
        ),
    )
    await worker_store.append_many(
        task_id,
        (
            (
                "model_output_delta",
                "complete",
                {"agent": "security:v2", "message_id": "provider-message"},
            ),
            (
                "model_output_delta",
                "complete",
                {"agent": "correctness:v2", "message_id": "provider-message"},
            ),
        ),
    )

    active_entries = await worker_store.list(task_id)
    assert [entry.sequence for entry in active_entries] == [1, 2, 3, 4]
    assert [entry.content for entry in active_entries] == [
        "Security ",
        "Correctness ",
        "complete",
        "complete",
    ]

    await worker_store.finalize(task_id)

    durable_entries = await durable.list(task_id)
    assert [entry.sequence for entry in durable_entries] == [1, 2, 3, 4]
    assert [entry.content for entry in durable_entries] == [
        "Security ",
        "Correctness ",
        "complete",
        "complete",
    ]


async def test_worker_transcript_starts_a_new_model_event_after_an_agent_boundary(
    tmp_path: Path,
) -> None:
    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    task_id = "review_" + "j" * 32
    metadata = {"agent": "security:v2", "message_id": "reused-provider-message"}

    await worker_store.append(task_id, "model_output_delta", "first response", metadata=metadata)
    await worker_store.append(
        task_id,
        "tool_call",
        "get_diff",
        metadata={"agent": "security:v2", "tool_name": "get_diff"},
    )
    await worker_store.append(task_id, "model_output_delta", "second response", metadata=metadata)

    entries = await worker_store.list(task_id)
    assert [entry.sequence for entry in entries] == [1, 2, 3]
    assert [entry.content for entry in entries] == [
        "first response",
        "get_diff",
        "second response",
    ]


async def test_worker_transcript_persists_rejected_tool_reason_to_outbox(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class RecordingOutbox:
        def __init__(self) -> None:
            self.records: list[tuple[str, str, dict[str, object]]] = []

        async def append(
            self, task_id: str, event_type: str, payload: dict[str, object]
        ) -> None:
            self.records.append((task_id, event_type, payload))

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    outbox = RecordingOutbox()
    worker_store = WorkerTranscriptStore(durable, rejection_events=outbox)
    task_id = "review_" + "k" * 32
    caplog.set_level(logging.WARNING, logger="codelens.worker.transcripts")

    await worker_store.append(
        task_id,
        "tool_result",
        '"validation error"',
        metadata={
            "agent": "security:v2",
            "tool_name": "comment",
            "tool_call_id": "call-rejected",
            "tool_outcome": "rejected",
            "tool_rejection_reason_code": "invalid_tool_arguments",
            "tool_rejection_reason": "Tool arguments failed schema validation.",
        },
    )

    assert outbox.records == [
        (
            task_id,
            "agent_tool_call.rejected.v2",
            {
                "agent": "security:v2",
                "tool_name": "comment",
                "tool_call_id": "call-rejected",
                "reason_code": "invalid_tool_arguments",
                "reason": "Tool arguments failed schema validation.",
            },
        )
    ]
    rejection_record = next(
        record
        for record in caplog.records
        if record.message == "Model-visible tool invocation rejected"
    )
    assert rejection_record.task_id == task_id
    assert rejection_record.reason_code == "invalid_tool_arguments"


async def test_worker_transcript_finalize_removes_lock(tmp_path: Path) -> None:
    """After finalize, the per-task lock must be dropped so it cannot accumulate."""

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    task_id = "review_" + "l" * 32

    await worker_store.append(task_id, "model_output_delta", "visible while running")
    assert task_id in worker_store._locks

    await worker_store.finalize(task_id)

    assert task_id not in worker_store._locks


async def test_worker_transcript_evict_inactive_flushes_inactive_task_transcripts(
    tmp_path: Path,
) -> None:
    """evict_inactive persists inactive task transcripts and drops their memory."""

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    active_task = "review_" + "m" * 32
    inactive_task = "review_" + "n" * 32

    await worker_store.append(active_task, "model_output_delta", "still running")
    await worker_store.append(inactive_task, "model_output_delta", "stale in memory")
    assert inactive_task in worker_store._locks

    evicted = await worker_store.evict_inactive({active_task})

    assert evicted == 1
    assert inactive_task not in worker_store._locks
    # Active task transcript is untouched in memory
    assert [entry.content for entry in await worker_store.list(active_task)] == ["still running"]
    # Inactive task entries were flushed to durable store before being dropped
    assert [entry.content for entry in await durable.list(inactive_task)] == ["stale in memory"]


async def test_worker_transcript_evict_inactive_preserves_active_task_transcripts(
    tmp_path: Path,
) -> None:
    """evict_inactive must not touch entries of tasks that are still active."""

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    task_id = "review_" + "o" * 32

    await worker_store.append(task_id, "model_output_delta", "running")

    evicted = await worker_store.evict_inactive({task_id})

    assert evicted == 0
    assert [entry.content for entry in await worker_store.list(task_id)] == ["running"]


async def test_execution_transcript_evict_locks_removes_only_stale(tmp_path: Path) -> None:
    """evict_locks drops locks for tasks no longer active; keeps active ones."""

    store = ExecutionTranscriptStore(tmp_path)
    active_task = "review_" + "p" * 32
    stale_task = "review_" + "q" * 32

    # Touch both tasks so locks are created
    await store.append(active_task, "lifecycle", "active")
    await store.append(stale_task, "lifecycle", "stale")
    assert active_task in store._locks
    assert stale_task in store._locks

    removed = store.evict_locks({active_task})

    assert removed == 1
    assert active_task in store._locks
    assert stale_task not in store._locks

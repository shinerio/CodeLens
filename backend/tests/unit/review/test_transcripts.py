from pathlib import Path

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
        metadata={"agent": "correctness:v1"},
    )

    entries = await store.list(task_id)

    assert [entry.sequence for entry in entries] == [1, 2]
    assert entries[1].redacted
    assert "secret-value" not in entries[1].content
    assert "another-secret" not in entries[1].content
    assert entries[1].metadata == {"agent": "correctness:v1"}


async def test_transcript_append_many_reads_and_writes_one_complete_batch(tmp_path: Path) -> None:
    store = ExecutionTranscriptStore(tmp_path)
    task_id = "review_" + "e" * 32

    await store.append_many(
        task_id,
        (
            ("model_started", "", {"agent": "correctness:v1"}),
            ("model_reasoning_delta", "Checking change map", {"agent": "correctness:v1"}),
            ("model_output_delta", "No defect found", {"agent": "correctness:v1"}),
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
        metadata={"agent": "correctness:v1", "message_id": "message-1"},
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

from pathlib import Path

from codelens.review.infrastructure.transcripts import ExecutionTranscriptStore


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


async def test_transcript_append_ignores_a_stale_legacy_temporary_file(tmp_path: Path) -> None:
    """A previous interrupted write cannot prevent a Worker from resuming a Review."""

    store = ExecutionTranscriptStore(tmp_path)
    task_id = "review_" + "d" * 32
    (tmp_path / f"{task_id}.tmp").write_text("partial", encoding="utf-8")

    await store.append(task_id, "lifecycle", "Review execution started")

    (entry,) = await store.list(task_id)
    assert entry.content == "Review execution started"

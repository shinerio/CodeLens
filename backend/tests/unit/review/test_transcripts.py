from pathlib import Path

from codelens.review.infrastructure.transcripts import (
    DeferredTranscriptStore,
    ExecutionTranscriptStore,
    LiveTranscriptCache,
    UnixTranscriptRelayServer,
    UnixWorkerTranscriptQueryClient,
    UnixWorkerTranscriptQueryServer,
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


async def test_transcript_append_ignores_a_stale_legacy_temporary_file(tmp_path: Path) -> None:
    """A previous interrupted write cannot prevent a Worker from resuming a Review."""

    store = ExecutionTranscriptStore(tmp_path)
    task_id = "review_" + "d" * 32
    (tmp_path / f"{task_id}.tmp").write_text("partial", encoding="utf-8")

    await store.append(task_id, "lifecycle", "Review execution started")

    (entry,) = await store.list(task_id)
    assert entry.content == "Review execution started"


async def test_deferred_transcript_is_live_in_api_memory_then_persisted_on_finalize(
    tmp_path: Path,
) -> None:
    """A running review avoids Artifact I/O while its API transcript stays observable."""

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    cache = LiveTranscriptCache()
    relay = UnixTranscriptRelayServer(tmp_path / "runtime" / "transcripts.sock", cache)
    await relay.start()
    try:
        task_id = "review_" + "f" * 32
        deferred = DeferredTranscriptStore(
            durable,
            tmp_path / "runtime" / "transcripts.sock",
            publish_interval=0,
        )

        await deferred.append(task_id, "model_output_delta", "still reviewing")

        live = await cache.get(task_id)
        assert live is not None
        assert [entry.content for entry in live] == ["still reviewing"]
        assert await durable.list(task_id) == ()

        await deferred.finalize(task_id)

        assert await cache.get(task_id) is None
        assert [entry.content for entry in await durable.list(task_id)] == ["still reviewing"]
    finally:
        await relay.close()


async def test_worker_transcript_query_reads_memory_until_terminal_persistence(
    tmp_path: Path,
) -> None:
    """API reads a running task from Worker memory and terminal data from its Artifact."""

    durable = ExecutionTranscriptStore(tmp_path / "artifacts")
    worker_store = WorkerTranscriptStore(durable)
    socket_path = tmp_path / "runtime" / "worker-transcripts.sock"
    server = UnixWorkerTranscriptQueryServer(socket_path, worker_store)
    client = UnixWorkerTranscriptQueryClient(socket_path)
    await server.start()
    try:
        task_id = "review_" + "g" * 32
        await worker_store.append(task_id, "model_output_delta", "visible while running")

        assert [entry.content for entry in await client.list(task_id)] == ["visible while running"]
        assert await durable.list(task_id) == ()

        await worker_store.finalize(task_id)

        assert await client.list(task_id) == ()
        assert [entry.content for entry in await durable.list(task_id)] == ["visible while running"]
    finally:
        await server.close()

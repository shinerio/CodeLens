import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codelens.findings.domain.models import FindingBatch
from codelens.review.application.orchestrator import PreparedReview, ReviewOrchestrator
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import UnvalidatedAgentOutput
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import (
    SqlCheckpointStore,
    SqlEventOutbox,
    SqlJobQueue,
    SqlReviewStore,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.reviewer_catalog.infrastructure.builtin_agents import correctness_agent
from codelens.workspace.domain.models import (
    BranchScope,
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotManifest,
    TaskWorktree,
)


class Runtime:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, _agent: object, _payload: bytes) -> UnvalidatedAgentOutput:
        self.calls += 1
        return UnvalidatedAgentOutput(
            b'{"schema_version":"1","findings":[]}', (), "fake", 0, 0, ()
        )


class Validator:
    async def validate(self, _payload: bytes) -> FindingBatch:
        return FindingBatch("1", ())


class Crash:
    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        self.triggered = False

    async def hit(self, boundary: str) -> None:
        if boundary == self.boundary and not self.triggered:
            self.triggered = True
            raise RuntimeError(f"crash:{boundary}")


def _task(tmp_path: Path) -> ReviewTask:
    return ReviewTask.create(
        task_id="review-restart",
        repository_id="repository-1",
        repository_realpath_hash="c" * 64,
        git_common_dir_hash="d" * 64,
        repository_path=tmp_path,
        target_paths=("src/state.py",),
        scope=BranchScope(base_ref="main", target_ref="feature"),
        target=ReviewTarget("a" * 40, "b" * 40, None),
        selected_agent_versions=("correctness:v1",),
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def _prepared(tmp_path: Path) -> PreparedReview:
    worktree = TaskWorktree(
        "worktree-1", "review-restart", "d" * 64, tmp_path, "b" * 40, "e" * 64
    )
    snapshot = ReviewSnapshot(
        "snapshot-1",
        worktree,
        ReviewTarget("a" * 40, "b" * 40, None),
        RepositoryFingerprint("b" * 40, "f" * 64, "1" * 64),
        SnapshotManifest((), (), ()),
        ChangeIndex(()),
    )
    agent = correctness_agent()
    return PreparedReview(snapshot, (agent,), {"correctness:v1": b"{}"})


def _orchestrator(
    database: Database,
    tmp_path: Path,
    runtime: Runtime,
    crash: Crash | None,
    *,
    store: SqlReviewStore | None = None,
) -> ReviewOrchestrator:
    workflow = store or SqlReviewStore(database)

    async def prepare(_task_id: str) -> PreparedReview:
        return _prepared(tmp_path)

    return ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=FilesystemRunArtifactStore(database, tmp_path / "outputs"),
        checkpoints=SqlCheckpointStore(database),
        validator_factory=lambda *_args: Validator(),
        completion=workflow,
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
        crash_injector=crash,
    )


@pytest.mark.parametrize(
    ("boundary", "expected_model_calls"),
    (
        ("before_model_invocation", 1),
        ("after_model_return", 2),
        ("after_artifact_write", 2),
        ("after_output_saved", 1),
        ("after_finding_completion", 1),
    ),
)
async def test_reopen_reuses_only_durable_output_and_terminal_event_is_singleton(
    tmp_path: Path,
    boundary: str,
    expected_model_calls: int,
) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}"
    runtime = Runtime()
    crash = Crash(boundary)
    database = Database(url)
    await database.migrate()
    await SqlReviewStore(database).create_with_job(_task(tmp_path))
    assert await SqlJobQueue(database).next_queued() is not None
    with pytest.raises(RuntimeError, match=f"crash:{boundary}"):
        await _orchestrator(database, tmp_path, runtime, crash).execute("review-restart")
    await database.dispose()

    reopened = Database(url)
    try:
        store = SqlReviewStore(reopened)
        await store.recover_after_singleton_restart()
        assert await SqlJobQueue(reopened).next_queued() is not None
        await _orchestrator(reopened, tmp_path, runtime, crash).execute("review-restart")

        assert runtime.calls == expected_model_calls
        assert (await store.get_review("review-restart")).status == "completed"  # type: ignore[union-attr]
        succeeded = [
            event
            for event in await SqlEventOutbox(reopened).list_after(
                "review-restart", after_event_id=0
            )
            if event.event_type == "agent.succeeded"
        ]
        assert len(succeeded) == 1
    finally:
        await reopened.dispose()


async def test_crash_inside_finding_transaction_rolls_back_then_reuses_output(
    tmp_path: Path,
) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}"
    runtime = Runtime()
    crash = Crash("after_finding_insert_attempt")
    database = Database(url)
    await database.migrate()
    crashing_store = SqlReviewStore(database, completion_hook=crash.hit)
    await crashing_store.create_with_job(_task(tmp_path))
    assert await SqlJobQueue(database).next_queued() is not None

    with pytest.raises(RuntimeError, match="crash:after_finding_insert_attempt"):
        await _orchestrator(
            database, tmp_path, runtime, None, store=crashing_store
        ).execute("review-restart")
    checkpoint = await SqlCheckpointStore(database).get(
        "review-restart", "correctness:v1:0:root"
    )
    assert checkpoint.status == "validating"
    await database.dispose()

    reopened = Database(url)
    try:
        store = SqlReviewStore(reopened)
        await store.recover_after_singleton_restart()
        assert await SqlJobQueue(reopened).next_queued() is not None
        await _orchestrator(reopened, tmp_path, runtime, None, store=store).execute(
            "review-restart"
        )
        assert runtime.calls == 1
    finally:
        await reopened.dispose()

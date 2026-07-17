import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingBatch,
    FindingDisposition,
    FindingSeverity,
    RuleReference,
    SourceLocation,
)
from codelens.review.domain.models import ReviewTask
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import (
    SqlCheckpointStore,
    SqlEventOutbox,
    SqlJobQueue,
    SqlReviewStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.workspace.domain.models import BranchScope, ReviewTarget, TaskWorktree


def _task(task_id: str, *, head: str = "b") -> ReviewTask:
    return ReviewTask.create(
        task_id=task_id,
        repository_id="repository-1",
        repository_realpath_hash="c" * 64,
        git_common_dir_hash="d" * 64,
        repository_path=Path("/tmp/repository-1"),
        target_paths=("src/state.py",),
        scope=BranchScope(base_ref="main", target_ref=f"feature-{head}"),
        target=ReviewTarget("a" * 40, head * 40, None),
        selected_agent_versions=("correctness:v1",),
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def _finding(finding_id: str) -> Finding:
    location = SourceLocation("src/state.py", 2, 2, "new", "a" * 64, False)
    return Finding(
        finding_id=finding_id,
        fingerprint=f"fingerprint-{finding_id}",
        reviewer_id="correctness",
        category="logic",
        title="Guard is inverted",
        severity=FindingSeverity.HIGH,
        disposition=FindingDisposition.BLOCKING,
        confidence=0.95,
        primary_location=location,
        related_locations=(),
        changed_hunk_id="hunk-1",
        change_origin=ChangeOrigin.INTRODUCED,
        evidence=(Evidence("excerpt", "Inverted return", None, "a" * 64),),
        impact="Ready state is reversed.",
        explanation="The changed expression negates the intended value.",
        reproduction=None,
        recommendation="Remove the negation.",
        suggested_patch=None,
        rule_sources=(RuleReference("REVIEW.md", "b" * 64),),
    )


async def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}")
    await database.migrate()
    return database


async def test_migration_and_task_job_event_creation_are_atomic(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    try:
        store = SqlReviewStore(database)
        jobs = SqlJobQueue(database)
        events = SqlEventOutbox(database)

        await store.create_with_job(_task("review-1"))

        assert (await jobs.get("review-1")).status == "queued"
        created_events = await events.list_after("review-1", after_event_id=0)
        assert [event.event_type for event in created_events] == ["review.created"]
        await events.append("review-1", "review.preparing", {"step": 1})
        await events.append("review-1", "review.ready", {"step": 2})
        resumed_events = await events.list_after(
            "review-1", after_event_id=created_events[0].event_id
        )
        assert [event.event_type for event in resumed_events] == [
            "review.preparing",
            "review.ready",
        ]
        assert [event.event_id for event in resumed_events] == sorted(
            event.event_id for event in resumed_events
        )
        with pytest.raises(IntegrityError):
            await store.create_with_job(_task("review-1"))
        assert await store.count_tasks() == 1

        await store.create_with_job(_task("review-2", head="c"))
        assert await store.count_tasks() == 2
        registry = SqlWorktreeRegistry(database, tmp_path)
        for task_id, worktree_id, head in (
            ("review-1", "worktree-1", "b"),
            ("review-2", "worktree-2", "c"),
        ):
            await registry.register(
                TaskWorktree(
                    worktree_id=worktree_id,
                    task_id=task_id,
                    repository_common_dir_hash="d" * 64,
                    root=(tmp_path / "worktrees" / task_id / "checkout").resolve(),
                    head_oid=head * 40,
                    ownership_token_hash="e" * 64,
                )
            )
        assert {item.worktree_id for item in await registry.list_all()} == {
            "worktree-1",
            "worktree-2",
        }
        async with database.engine.connect() as connection:
            rows = (await connection.execute(text("PRAGMA table_info(jobs)"))).mappings().all()
        columns = {str(row["name"]) for row in rows}
        assert not {"lease_owner", "lease_expires_at", "fencing_token"} & columns
    finally:
        await database.dispose()


async def test_restart_requeues_running_nodes_but_keeps_saved_outputs(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    try:
        store = SqlReviewStore(database)
        checkpoints = SqlCheckpointStore(database)
        jobs = SqlJobQueue(database)
        await store.create_with_job(_task("review-running"))
        claimed_job = await jobs.next_queued()
        assert claimed_job is not None
        assert (claimed_job.task_id, claimed_job.status) == ("review-running", "running")
        await store.create_with_job(_task("review-output"))
        await store.create_with_job(_task("review-terminal", head="c"))
        await checkpoints.ensure("review-running", "correctness:v1:0:root", "primary")
        await checkpoints.ensure("review-output", "correctness:v1:0:root", "primary")
        await checkpoints.ensure("review-terminal", "correctness:v1:0:root", "primary")
        await checkpoints.mark_running("review-running", "correctness:v1:0:root")
        await checkpoints.mark_running("review-output", "correctness:v1:0:root")
        await checkpoints.mark_running("review-terminal", "correctness:v1:0:root")
        await checkpoints.mark_output_saved(
            "review-output",
            "correctness:v1:0:root",
            "artifact-1",
            "a" * 64,
        )
        await checkpoints.mark_output_saved(
            "review-terminal",
            "correctness:v1:0:root",
            "artifact-2",
            "b" * 64,
        )
        await store.complete_agent_run(
            "review-terminal",
            "correctness:v1:0:root",
            FindingBatch("1", ()),
        )

        await store.recover_after_singleton_restart()

        running = await checkpoints.get("review-running", "correctness:v1:0:root")
        output = await checkpoints.get("review-output", "correctness:v1:0:root")
        terminal = await checkpoints.get("review-terminal", "correctness:v1:0:root")
        assert (await jobs.get("review-running")).status == "queued"
        assert running.status == "pending"
        assert output.status == "output_saved"
        assert terminal.status == "succeeded"
    finally:
        await database.dispose()


async def test_output_artifact_survives_reopen_and_fails_closed_on_hash_mismatch(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}"
    database = Database(database_url)
    await database.migrate()
    artifact_root = tmp_path / "artifacts"
    artifact_store = FilesystemRunArtifactStore(database, artifact_root)
    artifact = await artifact_store.write_output("run-1", b'{"schema_version":"1"}')
    await database.dispose()

    reopened = Database(database_url)
    try:
        reopened_store = FilesystemRunArtifactStore(reopened, artifact_root)
        assert await reopened_store.read_output(artifact.reference, artifact.content_hash) == (
            b'{"schema_version":"1"}'
        )
        artifact_files = await asyncio.to_thread(
            lambda: tuple(path for path in artifact_root.iterdir() if path.is_file())
        )
        await asyncio.to_thread(artifact_files[0].write_bytes, b"tampered")
        with pytest.raises(ValueError, match="hash mismatch"):
            await reopened_store.read_output(artifact.reference, artifact.content_hash)
    finally:
        await reopened.dispose()


async def test_finding_success_boundary_is_atomic(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    try:
        store = SqlReviewStore(database)
        checkpoints = SqlCheckpointStore(database)
        events = SqlEventOutbox(database)
        await store.create_with_job(_task("review-success"))
        await checkpoints.ensure("review-success", "node-success", "primary")
        await checkpoints.mark_running("review-success", "node-success")
        await checkpoints.mark_output_saved(
            "review-success",
            "node-success",
            "artifact-success",
            "a" * 64,
        )

        await store.complete_agent_run(
            "review-success",
            "node-success",
            FindingBatch("1", (_finding("finding-1"),)),
        )

        assert (await checkpoints.get("review-success", "node-success")).status == "succeeded"
        assert [item.finding_id for item in await store.list_findings("review-success")] == [
            "finding-1"
        ]
        assert any(
            event.event_type == "agent.succeeded"
            for event in await events.list_after("review-success", after_event_id=0)
        )

        await checkpoints.ensure("review-success", "node-rollback", "primary")
        await checkpoints.mark_running("review-success", "node-rollback")
        await checkpoints.mark_output_saved(
            "review-success",
            "node-rollback",
            "artifact-rollback",
            "b" * 64,
        )
        duplicate = _finding("finding-duplicate")
        with pytest.raises(IntegrityError):
            await store.complete_agent_run(
                "review-success",
                "node-rollback",
                FindingBatch("1", (duplicate, duplicate)),
            )

        assert (await checkpoints.get("review-success", "node-rollback")).status == "output_saved"
        assert "finding-duplicate" not in {
            item.finding_id for item in await store.list_findings("review-success")
        }
        rollback_events = await events.list_after("review-success", after_event_id=0)
        assert not any(
            event.event_type == "agent.succeeded"
            and event.payload.get("node_key") == "node-rollback"
            for event in rollback_events
        )
    finally:
        await database.dispose()


async def test_sqlite_busy_retries_the_whole_idempotent_transaction(tmp_path: Path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}",
        busy_timeout_ms=25,
        max_busy_retries=4,
    )
    await database.migrate()
    store = SqlReviewStore(database)
    try:
        async with database.engine.connect() as blocker:
            await blocker.exec_driver_sql("BEGIN IMMEDIATE")
            pending_create = asyncio.create_task(store.create_with_job(_task("review-busy")))
            await asyncio.sleep(0.06)
            await blocker.rollback()
            await pending_create

        assert await store.count_tasks() == 1
        assert (await SqlJobQueue(database).get("review-busy")).status == "queued"
        assert [
            event.event_type
            for event in await SqlEventOutbox(database).list_after("review-busy", after_event_id=0)
        ] == ["review.created"]
    finally:
        await database.dispose()

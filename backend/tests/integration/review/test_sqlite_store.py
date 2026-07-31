import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from alembic import command
from alembic.config import Config
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
from codelens.review.application.review_profiles import (
    CreateReviewProfileHandler,
    DeleteReviewProfileHandler,
    SetDefaultReviewProfileHandler,
)
from codelens.review.domain.agent_run import InvalidAgentRunStateError
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    BudgetProfile,
    ReviewProfileSnapshot,
)
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import (
    SqlCheckpointStore,
    SqlEventOutbox,
    SqlJobQueue,
    SqlRecentRepositoryStore,
    SqlReviewProfileRepository,
    SqlReviewStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.workspace.domain.models import BranchScope, ReviewTarget, TaskWorktree


def _task(
    task_id: str,
    *,
    head: str = "b",
    repository_path: Path = Path("/tmp/repository-1"),
    created_at: datetime = datetime(2026, 7, 17, tzinfo=UTC),
    review_profile: ReviewProfileSnapshot | None = None,
    idempotency_key: str | None = None,
    trigger_slot_key: str | None = None,
    supersede_policy: Literal["latest_snapshot", "preserve_all"] = "latest_snapshot",
) -> ReviewTask:
    return ReviewTask.create(
        task_id=task_id,
        repository_id="repository-1",
        repository_realpath_hash="c" * 64,
        git_common_dir_hash="d" * 64,
        repository_path=repository_path,
        target_paths=("src/state.py",),
        scope=BranchScope(base_ref="main", target_ref=f"feature-{head}"),
        target=ReviewTarget("a" * 40, head * 40, None),
        selected_agent_versions=("correctness:v1",),
        review_profile=review_profile,
        trigger_source="plugin" if idempotency_key else "manual",
        supersede_policy=supersede_policy if idempotency_key else None,
        idempotency_key=idempotency_key,
        trigger_slot_key=trigger_slot_key,
        created_at=created_at,
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
        rule_sources=(RuleReference("REVIEW.md", "b" * 64),),
    )


async def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}")
    await database.migrate()
    return database


def _migrate_to(database_path: Path, revision: str) -> None:
    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, revision)


async def test_review_profile_migration_upgrades_previous_head_and_seeds_empty_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    await asyncio.to_thread(_migrate_to, database_path, "0e0e42b05c24")
    await asyncio.to_thread(_migrate_to, database_path, "head")

    def read_profiles() -> list[tuple[str, int, str, int, str, str]]:
        with sqlite3.connect(database_path) as connection:
            return connection.execute(
                """
                SELECT profile_id, revision, name, is_default,
                       reviewer_selection_json, budget_profile
                FROM review_profiles
                """
            ).fetchall()

    assert await asyncio.to_thread(read_profiles) == [
        (
            "profile-balanced",
            1,
            "Balanced Review",
            1,
            '{"mode":"adaptive"}',
            "standard",
        )
    ]


async def test_selection_migration_backfills_legacy_fixed_request(tmp_path: Path) -> None:
    database_path = tmp_path / "selection-upgrade.sqlite3"
    await asyncio.to_thread(_migrate_to, database_path, "0005_review_profiles")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO review_tasks
            (task_id,repository_id,repository_realpath_hash,git_common_dir_hash,scope_json,
             base_oid,head_oid,status,selected_agent_versions_json,prompt_locale,
             cancellation_requested,created_at,updated_at)
            VALUES ('legacy','repo','a','b','{"type":"branch"}','c','d','created',
                    '["security:v1","performance:v1"]','en',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
    await asyncio.to_thread(_migrate_to, database_path, "head")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT selection_request_json,budget_profile,planning_context_json "
            "FROM review_tasks WHERE task_id='legacy'"
        ).fetchone()
    assert row == (
        '{"mode":"fixed","reviewer_versions":["security:v1","performance:v1"]}',
        "standard",
        None,
    )


async def test_triggered_create_deduplicates_and_supersedes_atomically(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    store = SqlReviewStore(database)
    try:
        profile = ReviewProfileSnapshot(
            AdaptiveReviewerSelection(), BudgetProfile.DEEP, "profile-auto", 2
        )
        first = _task(
            "review-first",
            review_profile=profile,
            idempotency_key="1" * 64,
            trigger_slot_key="a" * 64,
        )
        created, was_created = await store.create_triggered_with_job(first)
        duplicate, duplicate_created = await store.create_triggered_with_job(first)
        assert was_created and not duplicate_created and duplicate.task_id == created.task_id

        second = _task(
            "review-second",
            head="c",
            review_profile=profile,
            idempotency_key="2" * 64,
            trigger_slot_key="a" * 64,
        )
        await store.create_triggered_with_job(second)
        assert (await store.get_review("review-first")).status == "superseded"  # type: ignore[union-attr]
    finally:
        await database.dispose()


async def test_concurrent_identical_triggers_create_one_sqlite_task(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    store = SqlReviewStore(database)
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection(), BudgetProfile.STANDARD)
    try:
        left = _task(
            "review-left",
            review_profile=profile,
            idempotency_key="9" * 64,
            trigger_slot_key="8" * 64,
        )
        right = _task(
            "review-right",
            review_profile=profile,
            idempotency_key="9" * 64,
            trigger_slot_key="8" * 64,
        )
        results = await asyncio.gather(
            store.create_triggered_with_job(left), store.create_triggered_with_job(right)
        )
        assert {record.task_id for record, _ in results} in ({"review-left"}, {"review-right"})
        assert sorted(was_created for _, was_created in results) == [False, True]
    finally:
        await database.dispose()


async def test_latest_snapshot_cancels_running_but_preserves_history(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    store = SqlReviewStore(database)
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection(), BudgetProfile.STANDARD)
    try:
        running = _task(
            "review-running",
            review_profile=profile,
            idempotency_key="3" * 64,
            trigger_slot_key="7" * 64,
        )
        await store.create_triggered_with_job(running)
        for status in ("provisioning_worktree", "snapshotting", "preparing", "reviewing"):
            await store.transition(running.task_id, status)

        historical = _task(
            "review-history",
            review_profile=profile,
            idempotency_key="4" * 64,
            trigger_slot_key="7" * 64,
        )
        await store.create_triggered_with_job(historical)
        await store.fail(historical.task_id, "expected")

        newest = _task(
            "review-newest",
            head="e",
            review_profile=profile,
            idempotency_key="5" * 64,
            trigger_slot_key="7" * 64,
        )
        await store.create_triggered_with_job(newest)
        running_record = await store.get_review(running.task_id)
        history_record = await store.get_review(historical.task_id)
        assert running_record is not None and running_record.status == "reviewing"
        assert running_record.cancellation_requested
        assert history_record is not None and history_record.status == "failed"
    finally:
        await database.dispose()


async def test_latest_snapshot_requests_cancellation_after_job_claim_before_task_transition(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    store = SqlReviewStore(database)
    jobs = SqlJobQueue(database)
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection(), BudgetProfile.STANDARD)
    try:
        claimed = _task(
            "review-claimed",
            review_profile=profile,
            idempotency_key="a" * 64,
            trigger_slot_key="b" * 64,
        )
        await store.create_triggered_with_job(claimed)
        job = await jobs.next_queued()
        assert job is not None and job.task_id == claimed.task_id

        await store.create_triggered_with_job(
            _task(
                "review-after-claim",
                head="f",
                review_profile=profile,
                idempotency_key="c" * 64,
                trigger_slot_key="b" * 64,
            )
        )

        claimed_record = await store.get_review(claimed.task_id)
        claimed_job = await jobs.get(claimed.task_id)
        assert claimed_record is not None and claimed_record.status == "created"
        assert claimed_record.cancellation_requested
        assert claimed_job is not None and claimed_job.status == "running"
    finally:
        await database.dispose()


async def test_preserve_all_leaves_older_queued_task_unchanged(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    store = SqlReviewStore(database)
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection(), BudgetProfile.STANDARD)
    try:
        older = _task(
            "review-older",
            review_profile=profile,
            idempotency_key="6" * 64,
            trigger_slot_key="6" * 64,
            supersede_policy="preserve_all",
        )
        newer = _task(
            "review-newer",
            head="f",
            review_profile=profile,
            idempotency_key="7" * 64,
            trigger_slot_key="6" * 64,
            supersede_policy="preserve_all",
        )
        await store.create_triggered_with_job(older)
        await store.create_triggered_with_job(newer)
        older_record = await store.get_review(older.task_id)
        assert older_record is not None and older_record.status == "created"
        assert not older_record.cancellation_requested
    finally:
        await database.dispose()


async def test_review_profiles_keep_exactly_one_default_across_restart(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    repository = SqlReviewProfileRepository(database)
    try:
        create = CreateReviewProfileHandler(repository)
        custom = await create.handle(
            name="Deep Review",
            is_default=False,
            reviewer_selection=AdaptiveReviewerSelection(),
            budget_profile=BudgetProfile.DEEP,
        )
        switched = await SetDefaultReviewProfileHandler(repository).handle(
            custom.profile_id, expected_revision=custom.revision
        )
        profiles = await repository.list_review_profiles()
        assert [profile.profile_id for profile in profiles if profile.is_default] == [
            switched.profile_id
        ]
        with pytest.raises(ValueError, match="default profile"):
            await DeleteReviewProfileHandler(repository).handle(switched.profile_id)
        await DeleteReviewProfileHandler(repository).handle("profile-balanced")
    finally:
        await database.dispose()

    restarted = Database(f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}")
    try:
        profiles = await SqlReviewProfileRepository(restarted).list_review_profiles()
        assert [(profile.profile_id, profile.is_default) for profile in profiles] == [
            (switched.profile_id, True)
        ]
    finally:
        await restarted.dispose()


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
    finally:
        await database.dispose()


async def test_failed_review_retry_creates_an_independent_queued_task(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    try:
        store = SqlReviewStore(database)
        jobs = SqlJobQueue(database)
        events = SqlEventOutbox(database)
        original = _task(
            "review-original",
            review_profile=ReviewProfileSnapshot(
                AdaptiveReviewerSelection(), BudgetProfile.STANDARD, "profile-auto", 2
            ),
            idempotency_key="d" * 64,
            trigger_slot_key="e" * 64,
        )
        await store.create_with_job(original)
        await store.fail(original.task_id, "review_execution_failed")

        retried = await store.retry_failed_review(
            original.task_id,
            "review-retry",
            datetime(2026, 7, 18, tzinfo=UTC),
        )

        original_record = await store.get_review(original.task_id)
        retry_execution = await store.get_execution(retried.task_id)
        assert original_record is not None
        assert original_record.status == "failed"
        assert retried.task_id == "review-retry"
        assert retried.status == "created"
        assert (retried.base_oid, retried.head_oid) == (
            original.target.base_oid,
            original.target.head_oid,
        )
        assert retried.selected_agent_versions == original.selected_agent_versions
        assert retried.review_profile == original.review_profile
        assert retried.planning_context_hash == original.planning_context_hash
        assert retried.trigger_source == "manual"
        assert retried.supersede_policy is None
        assert retry_execution is not None
        assert retry_execution.repository_path == original.repository_path.resolve()
        assert retry_execution.target_paths == original.target_paths
        assert (await jobs.get(retried.task_id)).status == "queued"
        retry_events = await events.list_after(retried.task_id, after_event_id=0)
        assert [event.event_type for event in retry_events] == ["review.created"]
        assert retry_events[0].payload["retried_from_task_id"] == original.task_id
    finally:
        await database.dispose()


async def test_partial_review_transition_closes_the_job_and_emits_terminal_event(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    try:
        store = SqlReviewStore(database)
        jobs = SqlJobQueue(database)
        events = SqlEventOutbox(database)
        await store.create_with_job(_task("review-partial"))
        for status in (
            "provisioning_worktree",
            "snapshotting",
            "preparing",
            "reviewing",
            "validating",
            "synthesizing",
            "partial",
        ):
            await store.transition("review-partial", status)

        await store.complete_job("review-partial")

        review = await store.get_review("review-partial")
        assert review is not None
        assert review.status == "partial"
        assert (await jobs.get("review-partial")).status == "partial"
        assert (await events.list_after("review-partial", after_event_id=0))[-1].event_type == (
            "review.partial"
        )
    finally:
        await database.dispose()


async def test_retry_rejects_a_review_that_has_not_failed(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    try:
        store = SqlReviewStore(database)
        await store.create_with_job(_task("review-active"))

        with pytest.raises(InvalidAgentRunStateError, match="only failed reviews can retry"):
            await store.retry_failed_review(
                "review-active",
                "review-retry",
                datetime(2026, 7, 18, tzinfo=UTC),
            )

        assert await store.get_review("review-retry") is None
    finally:
        await database.dispose()


async def test_recent_repository_store_uses_a_ten_entry_lru(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    review_store = SqlReviewStore(database)
    recent_store = SqlRecentRepositoryStore(database)
    started_at = datetime(2026, 7, 17, tzinfo=UTC)
    try:
        assert await recent_store.get_limit() == 10
        for index in range(11):
            await review_store.create_with_job(
                _task(
                    f"review-{index}",
                    repository_path=tmp_path / f"repository-{index}",
                    created_at=started_at + timedelta(minutes=index),
                )
            )

        initial = await recent_store.list_recent_repositories(limit=10)
        assert [item.repository_path for item in initial] == [
            tmp_path / f"repository-{index}" for index in range(10, 0, -1)
        ]

        await review_store.create_with_job(
            _task(
                "review-reused",
                repository_path=tmp_path / "repository-1",
                created_at=started_at + timedelta(minutes=11),
            )
        )
        await review_store.create_with_job(
            _task(
                "review-new",
                repository_path=tmp_path / "repository-11",
                created_at=started_at + timedelta(minutes=12),
            )
        )

        promoted = await recent_store.list_recent_repositories(limit=10)
        assert [item.repository_path for item in promoted[:2]] == [
            tmp_path / "repository-11",
            tmp_path / "repository-1",
        ]
        assert tmp_path / "repository-2" not in {item.repository_path for item in promoted}

        await recent_store.update_limit(3)
        trimmed = await recent_store.list_recent_repositories(limit=3)
        assert [item.repository_path for item in trimmed] == [
            tmp_path / "repository-11",
            tmp_path / "repository-1",
            tmp_path / "repository-10",
        ]

        await recent_store.delete_recent_repository(tmp_path / "repository-1")
        await recent_store.delete_recent_repository(tmp_path / "repository-1")

        remaining = await recent_store.list_recent_repositories(limit=3)
        assert [item.repository_path for item in remaining] == [
            tmp_path / "repository-11",
            tmp_path / "repository-10",
        ]
        assert all(item.last_reviewed_at.tzinfo is UTC for item in remaining)
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
            "incomplete",
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
        saved = await checkpoints.get("review-output", "correctness:v1:0:root")
        assert saved.review_completion_status == "incomplete"
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

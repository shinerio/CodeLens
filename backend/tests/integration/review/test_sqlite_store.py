import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    FrozenAgentExecutionSpec,
    FrozenSkillActivation,
)
from codelens.capabilities.domain.skills import SkillActivationFacts
from codelens.findings.application.publish_findings import FindingPublisher
from codelens.findings.domain.candidates import (
    CandidateFinding,
    CandidateFindingBatch,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.clusters import FindingCluster
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
from codelens.findings.domain.verdict import VerdictDecision
from codelens.review.application.review_profiles import (
    CreateReviewProfileHandler,
    DeleteReviewProfileHandler,
    SetDefaultReviewProfileHandler,
)
from codelens.review.domain.agent_run import InvalidAgentRunStateError
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import ArtifactIdentity
from codelens.review.domain.review_plan import (
    ReviewPass,
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import (
    SqlAgentExecutionSpecStore,
    SqlCandidateFindingStore,
    SqlCheckpointStore,
    SqlEventOutbox,
    SqlJobQueue,
    SqlRecentRepositoryStore,
    SqlReviewPlanStore,
    SqlReviewProfileRepository,
    SqlReviewStore,
    SqlVerdictStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
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


def _multi_plan(task_id: str) -> ReviewPlan:
    reviewers = tuple(
        ReviewPlanNode.create(
            task_id=task_id,
            node_type=ReviewPlanNodeType.REVIEWER,
            agent_reference=reference,
            pass_index=ReviewPass.REVIEWER,
            shard_id="root",
            logical_attempt_group="primary",
            depends_on=(),
        )
        for reference in ("correctness:v2", "security:v1")
    )
    verifier = ReviewPlanNode.create(
        task_id=task_id,
        node_type=ReviewPlanNodeType.VERIFIER,
        agent_reference="review-verifier:v1",
        pass_index=ReviewPass.VERIFIER,
        shard_id="batch",
        logical_attempt_group="primary",
        depends_on=tuple(node.node_id for node in reviewers),
    )
    return ReviewPlan.create(
        task_id=task_id,
        selection_mode="fixed",
        reviewer_references=("correctness:v2", "security:v1"),
        nodes=(*reviewers, verifier),
        planner_reason=None,
    )


def _candidate(task_id: str, run_id: str, candidate_id: str) -> CandidateFinding:
    location = SourceLocation("src/state.py", 2, 2, "new", "a" * 64, False)
    return CandidateFinding(
        task_id=task_id,
        candidate_id=candidate_id,
        run_id=run_id,
        snapshot_id="snapshot-1",
        reviewer_reference="security:v1",
        category="authorization",
        title="Authorization is bypassed",
        severity=FindingSeverity.HIGH,
        primary_dimension="security",
        secondary_dimensions=(),
        evidence_strength=EvidenceStrength.DIRECT,
        impact_certainty=ImpactCertainty.CONFIRMED,
        reproducibility=Reproducibility.DETERMINISTIC,
        primary_location=location,
        related_locations=(),
        changed_hunk_id="hunk-1",
        existing_code_hash="b" * 64,
        evidence_hashes=("b" * 64,),
        content="The new branch skips authorization.",
        recommendation="Restore the authorization check.",
        fingerprint=f"fingerprint-{candidate_id}",
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


def _downgrade_upgrade(database_path: Path, revision: str) -> None:
    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.downgrade(config, revision)
    command.upgrade(config, "head")


async def test_review_profile_migration_upgrades_previous_head_and_seeds_empty_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    await asyncio.to_thread(_migrate_to, database_path, "0e0e42b05c24")
    await asyncio.to_thread(_migrate_to, database_path, "head")

    def read_profiles() -> list[tuple[str, int, str, int, str]]:
        with sqlite3.connect(database_path) as connection:
            return connection.execute(
                """
                SELECT profile_id, revision, name, is_default,
                       reviewer_selection_json
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
            "SELECT selection_request_json,planning_context_json "
            "FROM review_tasks WHERE task_id='legacy'"
        ).fetchone()
    assert row == (
        '{"mode":"fixed","reviewer_versions":["security:v1","performance:v1"]}',
        None,
    )


async def test_multi_agent_migration_preserves_legacy_confidence_and_round_trips(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "multi-agent-upgrade.sqlite3"
    await asyncio.to_thread(_migrate_to, database_path, "0006_review_selection_requests")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO review_tasks
            (task_id,repository_id,repository_realpath_hash,git_common_dir_hash,scope_json,
             base_oid,head_oid,status,selected_agent_versions_json,prompt_locale,
             cancellation_requested,created_at,updated_at)
            VALUES ('legacy','repo','a','b','{"type":"branch"}','c','d','created',
                    '["correctness:v1"]','en',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO findings
            (finding_id,task_id,node_key,fingerprint,payload_json,severity,confidence,path,
             start_line,created_at)
            VALUES ('finding-legacy','legacy','correctness:v1:0:root','fingerprint',
                    '{}','high',0.91,'src/state.py',2,CURRENT_TIMESTAMP)"""
        )

    await asyncio.to_thread(_migrate_to, database_path, "head")
    with sqlite3.connect(database_path) as connection:
        confidence = connection.execute(
            "SELECT confidence FROM findings WHERE finding_id='finding-legacy'"
        ).fetchone()
        nullable = next(
            row[3]
            for row in connection.execute("PRAGMA table_info(findings)").fetchall()
            if row[1] == "confidence"
        )
    assert confidence == (0.91,)
    assert nullable == 0

    await asyncio.to_thread(
        _downgrade_upgrade, database_path, "0006_review_selection_requests"
    )


async def test_plan_specs_and_audit_records_survive_restart_without_trusted_bodies(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}"
    database = Database(database_url)
    await database.migrate()
    task_id = "review-plan-audit"
    plan = _multi_plan(task_id)
    try:
        review_store = SqlReviewStore(database)
        await review_store.create_with_job(_task(task_id))
        artifact_store = FilesystemRunArtifactStore(database, tmp_path / "artifacts")
        agent = builtin_agent_catalog()["security:v1"]
        prompt_bytes = b"frozen security prompt"
        prompt = await artifact_store.write_output("prompt-security", prompt_bytes)
        spec = CapabilityResolver.testing().resolve(
            agent=agent,
            prompt_content_hash=hashlib.sha256(prompt_bytes).hexdigest(),
            facts=SkillActivationFacts((), ("src/state.py",)),
            execution_limits=AgentExecutionLimits.legacy_default(),
        )
        security_node = next(
            node for node in plan.nodes if node.agent_reference == "security:v1"
        )
        await SqlReviewPlanStore(database).save(
            plan,
            catalog_version="builtin-v1",
            capability_fingerprint="c" * 64,
        )
        await SqlAgentExecutionSpecStore(database).save(
            task_id=task_id,
            logical_node_id=security_node.node_id,
            execution_spec=spec,
            prompt_artifact_ref=prompt.reference,
            prompt_artifact_hash=prompt.content_hash,
            skill_artifacts=(),
        )
        skill_bytes = b"frozen declarative skill"
        skill_artifact = await artifact_store.write_output("skill-security", skill_bytes)
        conflicting_spec = FrozenAgentExecutionSpec.create(
            agent=spec.agent,
            capability_profile=spec.capability_profile,
            skill_policy=spec.skill_policy,
            prompt_content_hash=spec.prompt_content_hash,
            skills=(
                FrozenSkillActivation(
                    skill_id="security-checklist",
                    version=1,
                    content_hash=skill_artifact.content_hash,
                    activation_reason="language:python",
                    instruction_text=skill_bytes.decode(),
                ),
            ),
            execution_limits=spec.execution_limits,
        )
        with pytest.raises(ValueError, match="different frozen inputs"):
            await SqlAgentExecutionSpecStore(database).save(
                task_id=task_id,
                logical_node_id=security_node.node_id,
                execution_spec=conflicting_spec,
                prompt_artifact_ref=prompt.reference,
                prompt_artifact_hash=prompt.content_hash,
                skill_artifacts=(
                    ArtifactIdentity(skill_artifact.reference, skill_artifact.content_hash),
                ),
            )
        original_spec = await SqlAgentExecutionSpecStore(database).get(
            task_id, security_node.node_id
        )
        assert original_spec is not None and original_spec.skill_artifacts == ()
        checkpoints = SqlCheckpointStore(database)
        await checkpoints.ensure_plan_nodes(
            plan,
            capability_fingerprints={security_node.node_id: spec.fingerprint},
        )
    finally:
        await database.dispose()

    reopened = Database(database_url)
    try:
        stored_plan = await SqlReviewPlanStore(reopened).get(task_id)
        assert stored_plan is not None and stored_plan.plan == plan
        records = await SqlCheckpointStore(reopened).list_for_task(task_id)
        node_metadata = {
            (record.node_role, record.agent_version, record.shard_id) for record in records
        }
        assert node_metadata >= {
            ("reviewer", "security:v1", "root"),
            ("verifier", "review-verifier:v1", "batch"),
        }
        stored_spec = await SqlAgentExecutionSpecStore(reopened).get(
            task_id, security_node.node_id
        )
        assert stored_spec is not None
        assert stored_spec.fingerprint == spec.fingerprint
        assert stored_spec.prompt_artifact_hash == prompt.content_hash
        assert "frozen security prompt" not in stored_spec.spec_json
        assert agent.prompt_template not in stored_spec.spec_json
    finally:
        await reopened.dispose()


async def test_candidate_cluster_and_resolution_round_trip_and_atomic_completion(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    task_id = "review-candidates"
    plan = _multi_plan(task_id)
    try:
        review_store = SqlReviewStore(database)
        await review_store.create_with_job(_task(task_id))
        checkpoints = SqlCheckpointStore(database)
        await checkpoints.ensure_plan_nodes(plan)
        security_node = next(
            node for node in plan.nodes if node.agent_reference == "security:v1"
        )
        checkpoint = await checkpoints.get(task_id, security_node.node_id)
        await checkpoints.mark_running(task_id, security_node.node_id)
        await checkpoints.mark_output_saved(
            task_id, security_node.node_id, "artifact-candidate", "a" * 64
        )
        candidate = _candidate(task_id, checkpoint.run_id or "", "candidate-a")

        await review_store.complete_agent_run_with_candidates(
            task_id,
            security_node.node_id,
            CandidateFindingBatch((candidate,)),
            result_summary={"candidate_count": 1},
        )
        await review_store.complete_agent_run_with_candidates(
            task_id,
            security_node.node_id,
            CandidateFindingBatch((candidate,)),
            result_summary={"candidate_count": 1},
        )

        candidates = await SqlCandidateFindingStore(database).list_for_task(task_id)
        assert candidates == (candidate,)
        assert (await checkpoints.get(task_id, security_node.node_id)).status == "succeeded"
        cluster = FindingCluster(
            cluster_id="cluster-a",
            candidate_ids=(candidate.candidate_id,),
            canonical_candidate_id=candidate.candidate_id,
            title=candidate.title,
            category=candidate.category,
            severity=candidate.severity,
            content=candidate.content,
            recommendation=candidate.recommendation,
            primary_dimension=candidate.primary_dimension,
            secondary_dimensions=candidate.secondary_dimensions,
            evidence_strength=candidate.evidence_strength,
            impact_certainty=candidate.impact_certainty,
            reproducibility=candidate.reproducibility,
        )
        verdicts = SqlVerdictStore(database)
        await verdicts.save_clusters(task_id, "snapshot-1", (cluster,))
        with pytest.raises(ValueError, match="already belongs to another cluster"):
            await verdicts.save_clusters(
                task_id,
                "snapshot-1",
                (
                    FindingCluster(
                        cluster_id="cluster-b",
                        candidate_ids=(candidate.candidate_id,),
                        canonical_candidate_id=candidate.candidate_id,
                        title=candidate.title,
                        category=candidate.category,
                        severity=candidate.severity,
                        content=candidate.content,
                        recommendation=candidate.recommendation,
                        primary_dimension=candidate.primary_dimension,
                        secondary_dimensions=candidate.secondary_dimensions,
                        evidence_strength=candidate.evidence_strength,
                        impact_certainty=candidate.impact_certainty,
                        reproducibility=candidate.reproducibility,
                    ),
                ),
            )
        decision = VerdictDecision.accept(cluster_ids=(cluster.cluster_id,))
        await verdicts.save_decisions(task_id, (decision,))
        assert await verdicts.list_clusters(task_id) == (cluster,)
        assert await verdicts.list_decisions(task_id) == (decision,)

        verifier_node = next(
            node for node in plan.nodes if node.node_type is ReviewPlanNodeType.VERIFIER
        )
        await checkpoints.mark_running(task_id, verifier_node.node_id)
        await checkpoints.mark_output_saved(
            task_id, verifier_node.node_id, "artifact-verdict", "b" * 64
        )
        await review_store.complete_with_verdicts(
            task_id, verifier_node.node_id, (decision,)
        )

        events = await SqlEventOutbox(database).list_after(task_id, after_event_id=0)
        assert len([event for event in events if event.event_type == "agent.succeeded"]) == 2
        assert len(
            [event for event in events if event.event_type == "agent_run.completed"]
        ) == 2
    finally:
        await database.dispose()


async def test_verification_and_publication_replay_are_exactly_once(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    task_id = "review-verification-publication"
    plan = _multi_plan(task_id)
    try:
        store = SqlReviewStore(database)
        await store.create_with_job(_task(task_id))
        checkpoints = SqlCheckpointStore(database)
        await checkpoints.ensure_plan_nodes(plan)
        reviewer = next(
            node for node in plan.nodes if node.agent_reference == "security:v1"
        )
        reviewer_checkpoint = await checkpoints.get(task_id, reviewer.node_id)
        await checkpoints.mark_running(task_id, reviewer.node_id)
        await checkpoints.mark_output_saved(
            task_id, reviewer.node_id, "artifact-candidate", "a" * 64
        )
        candidate = _candidate(
            task_id, reviewer_checkpoint.run_id or "", "candidate-verify"
        )
        await store.complete_with_candidates(
            task_id, reviewer.node_id, CandidateFindingBatch((candidate,))
        )
        cluster = FindingCluster(
            cluster_id="cluster-verify",
            candidate_ids=(candidate.candidate_id,),
            canonical_candidate_id=candidate.candidate_id,
            title=candidate.title,
            category=candidate.category,
            severity=candidate.severity,
            content=candidate.content,
            recommendation=candidate.recommendation,
            primary_dimension=candidate.primary_dimension,
            secondary_dimensions=candidate.secondary_dimensions,
            evidence_strength=candidate.evidence_strength,
            impact_certainty=candidate.impact_certainty,
            reproducibility=candidate.reproducibility,
        )
        verdicts = SqlVerdictStore(database)
        await verdicts.save_clusters(task_id, "snapshot-1", (cluster,))
        decision = VerdictDecision.accept(cluster_ids=(cluster.cluster_id,))
        verifier = next(
            node for node in plan.nodes if node.agent_reference == "review-verifier:v1"
        )
        await checkpoints.mark_running(task_id, verifier.node_id)
        await checkpoints.mark_output_saved(
            task_id, verifier.node_id, "artifact-verification", "c" * 64
        )
        await store.complete_with_verdicts(task_id, verifier.node_id, (decision,))

        finding = FindingPublisher.build(
            task_id=task_id,
            candidates=(candidate,),
            verdicts=(decision,),
            clusters=(cluster,),
        )[0]

        await store.publish_verdict_findings(
            task_id, (decision,), ((cluster.cluster_id, finding),)
        )
        await store.publish_verdict_findings(
            task_id, (decision,), ((cluster.cluster_id, finding),)
        )

        assert await store.list_findings(task_id) == (finding,)
        events = await SqlEventOutbox(database).list_after(task_id, after_event_id=0)
        assert len([item for item in events if item.event_type == "finding.published"]) == 1
        assert any(
            item.event_type == "review.verdict_completed" for item in events
        )
    finally:
        await database.dispose()


async def test_candidate_completion_rolls_back_candidates_checkpoint_and_event(
    tmp_path: Path,
) -> None:
    async def fail_after_insert(boundary: str) -> None:
        if boundary == "after_candidate_insert_attempt":
            raise RuntimeError("injected candidate transaction failure")

    database = await _database(tmp_path)
    task_id = "review-candidate-rollback"
    try:
        store = SqlReviewStore(database, completion_hook=fail_after_insert)
        await store.create_with_job(_task(task_id))
        plan = _multi_plan(task_id)
        checkpoints = SqlCheckpointStore(database)
        await checkpoints.ensure_plan_nodes(plan)
        node = next(item for item in plan.nodes if item.agent_reference == "security:v1")
        checkpoint = await checkpoints.get(task_id, node.node_id)
        await checkpoints.mark_running(task_id, node.node_id)
        await checkpoints.mark_output_saved(
            task_id, node.node_id, "artifact-rollback", "d" * 64
        )
        candidate = _candidate(task_id, checkpoint.run_id or "", "candidate-rollback")

        with pytest.raises(RuntimeError, match="injected candidate"):
            await store.complete_agent_run_with_candidates(
                task_id, node.node_id, CandidateFindingBatch((candidate,))
            )

        assert await SqlCandidateFindingStore(database).list_for_task(task_id) == ()
        assert (await checkpoints.get(task_id, node.node_id)).status == "output_saved"
        assert not any(
            event.event_type == "agent.succeeded"
            for event in await SqlEventOutbox(database).list_after(task_id, after_event_id=0)
        )
    finally:
        await database.dispose()


async def test_triggered_create_deduplicates_and_supersedes_atomically(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    store = SqlReviewStore(database)
    try:
        profile = ReviewProfileSnapshot(
            AdaptiveReviewerSelection(), source_profile_id="profile-auto", source_profile_revision=2
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
        with pytest.raises(InvalidAgentRunStateError, match="terminal review"):
            await store.request_cancellation("review-first")
    finally:
        await database.dispose()


async def test_concurrent_identical_triggers_create_one_sqlite_task(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    store = SqlReviewStore(database)
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection())
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
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection())
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
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection())
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
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection())
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
                AdaptiveReviewerSelection(),
                source_profile_id="profile-auto",
                source_profile_revision=2,
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
        assert running.run_id is None
        assert running.node_role is None
        assert running.capability_fingerprint is None
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

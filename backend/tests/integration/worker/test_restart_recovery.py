import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    FrozenAgentExecutionSpec,
)
from codelens.capabilities.domain.skills import SkillActivationFacts
from codelens.findings.domain.models import FindingBatch
from codelens.review.application.orchestrator import PreparedReview, ReviewOrchestrator
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import UnvalidatedAgentOutput
from codelens.review.domain.review_plan import (
    ReviewPass,
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import (
    SqlAgentExecutionSpecStore,
    SqlCheckpointStore,
    SqlEventOutbox,
    SqlJobQueue,
    SqlReviewStore,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.reviewer_catalog.infrastructure.builtin_agents import correctness_agent
from codelens.worker.execution import (
    add_reviewer_plan_guidance,
    load_frozen_execution_specs,
)
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

    async def invoke(
        self,
        _execution_spec: FrozenAgentExecutionSpec,
        _payload: bytes,
        _snapshot: object,
        _prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        self.calls += 1
        return UnvalidatedAgentOutput(
            b'{"schema_version":"1","findings":[]}', (), "fake", 0, 0, ()
        )


class Validator:
    warnings: tuple[object, ...] = ()

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
    spec = CapabilityResolver.testing().resolve(
        agent=agent,
        prompt_content_hash=hashlib.sha256(agent.prompt_template.encode("utf-8")).hexdigest(),
        facts=SkillActivationFacts.empty(),
        execution_limits=AgentExecutionLimits.legacy_default(),
    )
    return PreparedReview(snapshot, (spec,), {"correctness:v1": b"{}"}, "en")


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


async def test_restart_uses_stored_spec_artifact_after_current_configuration_changes(
    tmp_path: Path,
) -> None:
    """Recovery follows frozen Artifact identities and fails closed on byte tampering."""

    url = f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}"
    artifact_root = tmp_path / "frozen-artifacts"
    database = Database(url)
    await database.migrate()
    await SqlReviewStore(database).create_with_job(_task(tmp_path))
    artifact_store = FilesystemRunArtifactStore(database, artifact_root)
    prompt_bytes = b"frozen prompt and skill policy"
    prompt_artifact = await artifact_store.write_output("prompt-run", prompt_bytes)
    agent = correctness_agent()
    spec = CapabilityResolver.testing().resolve(
        agent=replace(agent, prompt_template=prompt_bytes.decode()),
        prompt_content_hash=prompt_artifact.content_hash,
        facts=SkillActivationFacts.empty(),
        execution_limits=AgentExecutionLimits.legacy_default(),
    )
    await SqlAgentExecutionSpecStore(database).save(
        task_id="review-restart",
        logical_node_id="node-reviewer",
        execution_spec=spec,
        prompt_artifact_ref=prompt_artifact.reference,
        prompt_artifact_hash=prompt_artifact.content_hash,
        skill_artifacts=(),
    )
    await database.dispose()

    # Simulate mutable Catalog/prompt configuration changing after task creation.
    changed_current_agent = replace(agent, prompt_template="new current prompt")
    assert changed_current_agent.prompt_template != prompt_bytes.decode()

    reopened = Database(url)
    try:
        stored = await SqlAgentExecutionSpecStore(reopened).get(
            "review-restart", "node-reviewer"
        )
        assert stored is not None
        assert stored.fingerprint == spec.fingerprint
        reopened_artifacts = FilesystemRunArtifactStore(reopened, artifact_root)
        assert await reopened_artifacts.read_output(
            stored.prompt_artifact_ref, stored.prompt_artifact_hash
        ) == prompt_bytes
        hydrated = await load_frozen_execution_specs(
            "review-restart",
            SqlAgentExecutionSpecStore(reopened),
            reopened_artifacts,
        )
        assert hydrated["node-reviewer"] == spec

        artifact_file = next(path for path in artifact_root.iterdir() if path.is_file())
        await asyncio.to_thread(artifact_file.write_bytes, b"tampered current bytes")
        with pytest.raises(ValueError, match="hash mismatch"):
            await reopened_artifacts.read_output(
                stored.prompt_artifact_ref, stored.prompt_artifact_hash
            )
    finally:
        await reopened.dispose()


def test_adaptive_guidance_preserves_complete_reviewer_snapshot_scope() -> None:
    base_payload = json.dumps(
        {
            "review_files": [
                {"path": "src/auth.py"},
                {"path": "src/payments.py"},
            ],
            "repository_instructions": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    payload = json.loads(
        add_reviewer_plan_guidance(
            base_payload,
            reason_codes=("security-risk",),
            focus_paths=("src/auth.py",),
        )
    )

    assert payload["role_context"] == {
        "planner_guidance": {
            "focus_paths": ["src/auth.py"],
            "reason_codes": ["security-risk"],
        }
    }
    assert [item["path"] for item in payload["review_files"]] == [
        "src/auth.py",
        "src/payments.py",
    ]


async def test_restart_preserves_one_saved_reviewer_and_requeues_running_peer(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}"
    database = Database(database_url)
    await database.migrate()
    await SqlReviewStore(database).create_with_job(_task(tmp_path))
    reviewers = tuple(
        ReviewPlanNode.create(
            task_id="review-restart",
            node_type=ReviewPlanNodeType.REVIEWER,
            agent_reference=reference,
            pass_index=ReviewPass.REVIEWER,
            shard_id="root",
            logical_attempt_group="primary",
            depends_on=(),
        )
        for reference in ("security:v1", "performance:v1")
    )
    verifier = ReviewPlanNode.create(
        task_id="review-restart",
        node_type=ReviewPlanNodeType.VERIFIER,
        agent_reference="review-verifier:v1",
        pass_index=ReviewPass.VERIFIER,
        shard_id="batch",
        logical_attempt_group="primary",
        depends_on=tuple(sorted(node.node_id for node in reviewers)),
    )
    plan = ReviewPlan.create(
        task_id="review-restart",
        selection_mode="fixed",
        reviewer_references=("security:v1", "performance:v1"),
        nodes=(*reviewers, verifier),
        planner_reason=None,
    )
    checkpoints = SqlCheckpointStore(database)
    await checkpoints.ensure_plan_nodes(plan)
    await checkpoints.mark_running("review-restart", reviewers[0].node_id)
    await checkpoints.mark_output_saved(
        "review-restart", reviewers[0].node_id, "artifact-saved", "a" * 64
    )
    await checkpoints.mark_running("review-restart", reviewers[1].node_id)
    await database.dispose()

    reopened = Database(database_url)
    try:
        await SqlReviewStore(reopened).recover_after_singleton_restart()
        recovered = SqlCheckpointStore(reopened)
        assert (
            await recovered.get("review-restart", reviewers[0].node_id)
        ).status == "output_saved"
        assert (
            await recovered.get("review-restart", reviewers[1].node_id)
        ).status == "pending"
    finally:
        await reopened.dispose()

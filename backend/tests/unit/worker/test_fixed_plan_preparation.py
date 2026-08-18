import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    canonical_execution_payload,
)
from codelens.capabilities.domain.skills import SkillActivationFacts
from codelens.findings.domain.existing_findings import ExistingFinding, ExistingFindingSet
from codelens.review.domain.ports import (
    AgentExecutionSpecRecord,
    AgentRuntimeEvent,
    ReviewExecutionRecord,
    RunOutputArtifact,
    UnvalidatedAgentOutput,
)
from codelens.review.domain.review_plan import ReviewPlanNodeType
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
from codelens.worker.execution import WorkerReviewExecutor
from codelens.workspace.domain.models import (
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotManifest,
    TaskWorktree,
)
from codelens.workspace.domain.review_file_scope import ReviewFileScope


class _ReviewStore:
    def __init__(self, record: ReviewExecutionRecord, checkpoints: Any | None = None) -> None:
        self._record = record
        self._checkpoints = checkpoints

    async def get_execution(self, _task_id: str) -> ReviewExecutionRecord:
        return self._record

    async def complete_planner_run(
        self, _task_id: str, _node_key: str, selection: tuple[str, ...]
    ) -> None:
        self.planner_selection = selection
        if self._checkpoints is not None:
            self._checkpoints.record.status = "succeeded"


class _PlanStore:
    def __init__(self) -> None:
        self.record: Any | None = None

    async def get(self, _task_id: str) -> Any | None:
        return self.record

    async def save(self, plan: Any, **metadata: Any) -> Any:
        self.record = SimpleNamespace(plan=plan, **metadata)
        return self.record


class _ExecutionSpecStore:
    def __init__(self) -> None:
        self.records: dict[str, AgentExecutionSpecRecord] = {}

    async def list_for_task(self, _task_id: str) -> tuple[AgentExecutionSpecRecord, ...]:
        return tuple(self.records.values())

    async def save(self, **values: Any) -> AgentExecutionSpecRecord:
        execution_spec = values["execution_spec"]
        record = AgentExecutionSpecRecord(
            task_id=values["task_id"],
            logical_node_id=values["logical_node_id"],
            spec_json=canonical_execution_payload(
                execution_spec.agent,
                execution_spec.capability_profile,
                execution_spec.skill_policy,
                execution_spec.prompt_content_hash,
                execution_spec.skills,
                execution_spec.execution_limits,
            ).decode(),
            fingerprint=execution_spec.fingerprint,
            prompt_artifact_ref=values["prompt_artifact_ref"],
            prompt_artifact_hash=values["prompt_artifact_hash"],
            skill_artifacts=values["skill_artifacts"],
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
        self.records[record.logical_node_id] = record
        return record


class _Artifacts:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    async def write_output(self, run_id: str, payload: bytes) -> RunOutputArtifact:
        content_hash = hashlib.sha256(payload).hexdigest()
        reference = f"artifact_{len(self.payloads):032x}"
        self.payloads[reference] = payload
        return RunOutputArtifact(reference, content_hash, len(payload))

    async def read_output(self, reference: str, expected_hash: str) -> bytes:
        payload = self.payloads[reference]
        assert hashlib.sha256(payload).hexdigest() == expected_hash
        return payload


class _Checkpoints:
    def __init__(self) -> None:
        self.record = SimpleNamespace(
            status="pending",
            artifact_ref=None,
            artifact_hash=None,
            run_id="run_" + "1" * 64,
        )

    async def ensure(self, *_args: Any) -> None:
        return None

    async def ensure_plan_node(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def get(self, *_args: Any) -> Any:
        return self.record

    async def mark_running(self, *_args: Any) -> None:
        self.record.status = "running"

    async def mark_output_saved(
        self,
        _task_id: str,
        _node_key: str,
        reference: str,
        content_hash: str,
        _review_completion_status: str,
    ) -> None:
        self.record.status = "output_saved"
        self.record.artifact_ref = reference
        self.record.artifact_hash = content_hash

    async def mark_validating(self, *_args: Any) -> None:
        self.record.status = "validating"


def _snapshot(tmp_path: Path) -> ReviewSnapshot:
    worktree = TaskWorktree(
        "worktree-fixed",
        "review-fixed",
        "d" * 64,
        tmp_path,
        "b" * 40,
        "e" * 64,
    )
    return ReviewSnapshot(
        "snapshot-fixed",
        worktree,
        ReviewTarget("a" * 40, "b" * 40, None),
        RepositoryFingerprint("b" * 40, "f" * 64, "1" * 64),
        SnapshotManifest(ReviewFileScope.include_all(("src/review.py",))),
        ChangeIndex(()),
    )


async def test_prepare_compiles_fixed_team_plan_without_planner(
    tmp_path: Path, monkeypatch: Any
) -> None:
    profile = ReviewProfileSnapshot(FixedReviewerSelection(("correctness:v2", "security:v2")))
    existing_findings = ExistingFindingSet.from_findings(
        (
            ExistingFinding(
                source_id="local",
                finding_id="finding-1",
                title="Already reported",
                content="Do not report this issue again.",
            ),
        )
    )
    record = ReviewExecutionRecord(
        task_id="review-fixed",
        repository_path=tmp_path,
        repository_realpath_hash="c" * 64,
        git_common_dir_hash="d" * 64,
        base_oid="a" * 40,
        head_oid="b" * 40,
        scope_type="branch",
        base_ref="main",
        target_ref="feature",
        overlay_hash=None,
        overlay_artifact_ref=None,
        candidate_paths=("src/review.py",),
        file_exclusion_policy_json=('{"exclude_binary":true,"path_regexes":[],"suffixes":[]}'),
        file_exclusion_policy_hash=(
            "f135f14995e69bb776fd5c18af7fa0d19e45f867501b3274e9cb38cfbc7676c3"
        ),
        selected_agent_versions=("correctness:v2", "security:v2"),
        prompt_locale="en",
        status="provisioning_worktree",
        cancellation_requested=False,
        review_profile=profile,
        existing_findings_json=existing_findings.canonical_json,
        existing_findings_hash=existing_findings.content_hash,
    )
    snapshot = _snapshot(tmp_path)
    plan_store = _PlanStore()
    spec_store = _ExecutionSpecStore()
    artifacts = _Artifacts()
    executor = object.__new__(WorkerReviewExecutor)
    executor._review_store = _ReviewStore(record)
    executor._repository_inspector = SimpleNamespace(inspect=AsyncMock())
    executor._worktree_registry = SimpleNamespace(get=AsyncMock(return_value=snapshot.worktree))
    executor._worktree_lifecycle = SimpleNamespace(verify_ownership=AsyncMock())
    executor._snapshot_service = SimpleNamespace(
        resolve_instructions=AsyncMock(return_value=()),
        freeze=AsyncMock(return_value=snapshot),
    )
    executor._provider_config = SimpleNamespace(
        load=AsyncMock(
            return_value=SimpleNamespace(
                max_agent_turns=20,
                max_tool_calls=120,
                max_tokens=16_000,
                agent_timeout=600.0,
            )
        )
    )
    executor._tool_limits_service = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(max_read_bytes=65_536))
    )
    executor._review_plan_store = plan_store
    executor._execution_spec_store = spec_store
    executor._output_artifacts = artifacts
    executor._context_builder = SimpleNamespace(
        build=lambda *_args: SimpleNamespace(
            canonical_bytes=lambda: json.dumps(
                {"repository_instructions": [], "review_files": []},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    )

    async def no_repository_check(_record: ReviewExecutionRecord) -> None:
        return None

    async def execution_specs(references: tuple[str, ...], *_args: Any) -> tuple[Any, ...]:
        catalog = builtin_agent_catalog()
        limits = AgentExecutionLimits.default()
        return tuple(
            CapabilityResolver.testing().resolve(
                agent=catalog[reference],
                prompt_content_hash=hashlib.sha256(
                    catalog[reference].prompt_template.encode()
                ).hexdigest(),
                facts=SkillActivationFacts.empty(),
                execution_limits=limits,
            )
            for reference in references
        )

    monkeypatch.setattr(executor, "_validate_repository", no_repository_check)
    monkeypatch.setattr(executor, "_execution_specs", execution_specs)

    prepared = await executor.prepare("review-fixed")

    assert prepared.plan is not None
    assert prepared.plan.reviewer_references == ("correctness:v2", "security:v2")
    assert all(node.node_type is not ReviewPlanNodeType.PLANNER for node in prepared.plan.nodes)
    assert {node.node_type for node in prepared.plan.nodes} == {
        ReviewPlanNodeType.REVIEWER,
        ReviewPlanNodeType.VERIFIER,
    }
    assert set(spec_store.records) == {node.node_id for node in prepared.plan.nodes}
    assert all(
        json.loads(payload)["role_context"]["_host_run_id"].startswith("run_")
        for payload in prepared.input_payloads.values()
    )
    assert all(
        json.loads(payload)["role_context"]["existing_findings"]["findings"]
        == [existing_findings.items[0].as_payload()]
        for payload in prepared.input_payloads.values()
    )
    first_payloads = prepared.input_payloads
    artifact_count = len(artifacts.payloads)

    recovered = await executor.prepare("review-fixed")

    assert recovered.plan == prepared.plan
    assert recovered.input_payloads == first_payloads
    assert len(artifacts.payloads) == artifact_count


async def test_prepare_runs_and_persists_adaptive_planner_before_reviewers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection())
    existing_findings = ExistingFindingSet.from_findings(
        (
            ExistingFinding(
                source_id="github",
                finding_id="discussion-42",
                title="Already reported",
                content="Do not report this issue again.",
                path="src/review.py",
                side="new",
                start_line=1,
                end_line=1,
                existing_code="return review",
            ),
        )
    )
    record = ReviewExecutionRecord(
        task_id="review-adaptive",
        repository_path=tmp_path,
        repository_realpath_hash="c" * 64,
        git_common_dir_hash="d" * 64,
        base_oid="a" * 40,
        head_oid="b" * 40,
        scope_type="branch",
        base_ref="main",
        target_ref="feature",
        overlay_hash=None,
        overlay_artifact_ref=None,
        candidate_paths=("src/review.py",),
        file_exclusion_policy_json=('{"exclude_binary":true,"path_regexes":[],"suffixes":[]}'),
        file_exclusion_policy_hash=(
            "f135f14995e69bb776fd5c18af7fa0d19e45f867501b3274e9cb38cfbc7676c3"
        ),
        selected_agent_versions=(),
        prompt_locale="en",
        status="provisioning_worktree",
        cancellation_requested=False,
        review_profile=profile,
        existing_findings_json=existing_findings.canonical_json,
        existing_findings_hash=existing_findings.content_hash,
    )
    snapshot = _snapshot(tmp_path)
    checkpoints = _Checkpoints()
    review_store = _ReviewStore(record, checkpoints)
    plan_store = _PlanStore()
    spec_store = _ExecutionSpecStore()
    artifacts = _Artifacts()
    planner_output = UnvalidatedAgentOutput(
        b'{"reviewer_references":["general:v2"],"schema_version":"2"}',
        (),
        "fake",
        11,
        7,
        (),
    )

    async def invoke_stream(*args: Any) -> UnvalidatedAgentOutput:
        sink = args[-1]
        await sink(AgentRuntimeEvent("prompt", "planner prompt", {"model_name": "fake"}))
        await sink(AgentRuntimeEvent("model_started", "", {}))
        await sink(AgentRuntimeEvent("tool_call", "{}", {"tool_name": "finalize_plan"}))
        await sink(AgentRuntimeEvent("tool_result", '{"accepted":true}', {}))
        await sink(AgentRuntimeEvent("model_raw_output", "raw planner response", {}))
        await sink(AgentRuntimeEvent("model_completed", "", {}))
        return planner_output

    runtime = SimpleNamespace(
        invoke=AsyncMock(),
        invoke_stream=AsyncMock(side_effect=invoke_stream),
    )
    executor = object.__new__(WorkerReviewExecutor)
    executor._review_store = review_store
    executor._repository_inspector = SimpleNamespace(inspect=AsyncMock())
    executor._worktree_registry = SimpleNamespace(get=AsyncMock(return_value=snapshot.worktree))
    executor._worktree_lifecycle = SimpleNamespace(verify_ownership=AsyncMock())
    executor._snapshot_service = SimpleNamespace(
        resolve_instructions=AsyncMock(return_value=()), freeze=AsyncMock(return_value=snapshot)
    )
    executor._provider_config = SimpleNamespace(
        load=AsyncMock(
            return_value=SimpleNamespace(
                max_agent_turns=20,
                max_tool_calls=120,
                max_tokens=16_000,
                agent_timeout=600.0,
            )
        )
    )
    executor._tool_limits_service = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(max_read_bytes=65_536))
    )
    executor._review_plan_store = plan_store
    executor._execution_spec_store = spec_store
    executor._output_artifacts = artifacts
    executor._checkpoints = checkpoints
    executor._runtime = runtime
    executor._transcripts = SimpleNamespace(append=AsyncMock())
    executor._context_builder = SimpleNamespace(
        build=lambda *_args: SimpleNamespace(
            canonical_bytes=lambda: b'{"repository_instructions":[],"review_files":[]}'
        )
    )

    async def no_repository_check(_record: ReviewExecutionRecord) -> None:
        return None

    async def execution_specs(references: tuple[str, ...], *_args: Any) -> tuple[Any, ...]:
        catalog = builtin_agent_catalog()
        return tuple(
            CapabilityResolver.testing().resolve(
                agent=catalog[reference],
                prompt_content_hash=hashlib.sha256(
                    catalog[reference].prompt_template.encode()
                ).hexdigest(),
                facts=SkillActivationFacts.empty(),
                execution_limits=AgentExecutionLimits.default(),
            )
            for reference in references
        )

    monkeypatch.setattr(executor, "_validate_repository", no_repository_check)
    monkeypatch.setattr(executor, "_execution_specs", execution_specs)

    prepared = await executor.prepare("review-adaptive")

    assert prepared.plan is not None
    assert prepared.plan.reviewer_references == ("general:v2",)
    assert prepared.plan.nodes[0].node_type is ReviewPlanNodeType.PLANNER
    assert runtime.invoke.await_count == 0
    assert runtime.invoke_stream.await_count == 1
    planner_payload = json.loads(runtime.invoke_stream.await_args.args[1])
    assert "existing_findings" not in planner_payload.get("role_context", {})
    for node in prepared.plan.nodes:
        node_payload = json.loads(prepared.input_payloads[node.node_id])
        if node.node_type is ReviewPlanNodeType.PLANNER:
            assert "existing_findings" not in node_payload["role_context"]
        else:
            assert node_payload["role_context"]["existing_findings"]["findings"] == [
                existing_findings.items[0].as_payload()
            ]
    planner_records = executor._transcripts.append.await_args_list
    assert {call.args[1] for call in planner_records} >= {
        "prompt",
        "model_started",
        "tool_call",
        "tool_result",
        "model_raw_output",
        "model_completed",
        "model_output",
    }
    for call in planner_records:
        metadata = call.kwargs["metadata"]
        assert metadata["agent"] == "review-planner:v2"
        assert metadata["node_id"] == prepared.plan.nodes[0].node_id
        assert metadata["run_id"] == checkpoints.record.run_id
    assert review_store.planner_selection == ("general:v2",)
    assert set(spec_store.records) == {node.node_id for node in prepared.plan.nodes}

    recovered = await executor.prepare("review-adaptive")

    assert recovered.plan == prepared.plan
    assert runtime.invoke_stream.await_count == 1

    plan_store.record = None
    checkpoints.record.status = "output_saved"
    resumed = await executor.prepare("review-adaptive")

    assert resumed.plan == prepared.plan
    assert runtime.invoke_stream.await_count == 1


async def test_adaptive_planner_failure_retains_observable_events(tmp_path: Path) -> None:
    catalog = builtin_agent_catalog()
    planner_spec = CapabilityResolver.testing().resolve(
        agent=catalog["review-planner:v2"],
        prompt_content_hash=hashlib.sha256(
            catalog["review-planner:v2"].prompt_template.encode()
        ).hexdigest(),
        facts=SkillActivationFacts.empty(),
        execution_limits=AgentExecutionLimits.default(),
    )

    async def invoke_stream(*args: Any) -> UnvalidatedAgentOutput:
        sink = args[-1]
        await sink(AgentRuntimeEvent("prompt", "safe planner prompt", {"model_name": "fake"}))
        await sink(AgentRuntimeEvent("model_started", "", {}))
        raise RuntimeError("provider failed")

    transcripts = SimpleNamespace(append=AsyncMock())
    executor = object.__new__(WorkerReviewExecutor)
    executor._runtime = SimpleNamespace(invoke_stream=invoke_stream)
    executor._transcripts = transcripts

    with pytest.raises(RuntimeError, match="provider failed"):
        await executor._invoke_planner_observably(
            task_id="review-adaptive",
            node_id="node-planner",
            run_id="run_" + "1" * 64,
            execution_spec=planner_spec,
            input_payload=b"{}",
            snapshot=_snapshot(tmp_path),
            prompt_locale="en",
        )

    assert [call.args[1] for call in transcripts.append.await_args_list] == [
        "prompt",
        "model_started",
    ]
    assert all(
        call.kwargs["metadata"] == {
            "agent": "review-planner:v2",
            "node_id": "node-planner",
            "run_id": "run_" + "1" * 64,
            **({"model_name": "fake"} if call.args[1] == "prompt" else {}),
        }
        for call in transcripts.append.await_args_list
    )

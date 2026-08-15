import asyncio
import hashlib
import json
from dataclasses import dataclass

import pytest

from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    FrozenAgentExecutionSpec,
)
from codelens.capabilities.domain.skills import SkillActivationFacts
from codelens.findings.domain.candidates import CandidateFindingBatch
from codelens.review.application.dag_scheduler import (
    PersistedDagScheduler,
    reviewer_stage_outcome,
)
from codelens.review.application.orchestrator import (
    PreparedReview,
    ReviewOrchestrator,
)
from codelens.review.domain.errors import ToolLoopDetectedError
from codelens.review.domain.ports import (
    AgentResponseDiagnostic,
    AgentRuntimeEvent,
    FindingValidationWarning,
    RunOutputArtifact,
    UnvalidatedAgentOutput,
)
from codelens.review.domain.review_plan import (
    ReviewPass,
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import (
    builtin_agent_catalog,
    correctness_agent,
)
from codelens.workspace.domain.models import (
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotManifest,
    TaskWorktree,
)
from codelens.workspace.domain.review_file_scope import ReviewFileScope


@dataclass
class MemoryCheckpoint:
    status: str = "pending"
    artifact_ref: str | None = None
    artifact_hash: str | None = None
    review_completion_status: str = "complete"
    execution_attempts: int = 0
    validation_attempts: int = 0


class MemoryWorkflow:
    def __init__(self, status: str = "created") -> None:
        self.status = status
        self.transitions: list[str] = []
        self.is_cancellation_requested = False
        self.job_completed = False
        self.is_partial = False

    async def get_status(self, _task_id: str) -> str:
        return self.status

    async def transition(self, _task_id: str, status: str, **_values: str) -> None:
        self.status = status
        self.transitions.append(status)

    async def cancellation_requested(self, _task_id: str) -> bool:
        return self.is_cancellation_requested

    async def cancel(self, _task_id: str) -> None:
        self.status = "canceled"

    async def fail(self, _task_id: str, _error_code: str) -> None:
        self.status = "failed"

    async def interrupt(self, _task_id: str) -> None:
        return None

    async def complete_job(self, _task_id: str) -> None:
        self.job_completed = True

    async def mark_partial_coverage(self, _task_id: str) -> None:
        self.is_partial = True

    async def has_partial_coverage(self, _task_id: str) -> bool:
        return self.is_partial


class MemoryCheckpoints:
    def __init__(self) -> None:
        self.value = MemoryCheckpoint()

    async def ensure(self, _task_id: str, _node_key: str, _group: str) -> None:
        return None

    async def get(self, _task_id: str, _node_key: str) -> MemoryCheckpoint:
        return self.value

    async def mark_running(self, _task_id: str, _node_key: str) -> None:
        assert self.value.status == "pending"
        self.value.status = "running"
        self.value.execution_attempts += 1

    async def mark_output_saved(
        self,
        _task_id: str,
        _node_key: str,
        reference: str,
        content_hash: str,
        review_completion_status: str = "complete",
    ) -> None:
        assert self.value.status == "running"
        self.value.status = "output_saved"
        self.value.artifact_ref = reference
        self.value.artifact_hash = content_hash
        self.value.review_completion_status = review_completion_status

    async def mark_validating(self, _task_id: str, _node_key: str) -> None:
        assert self.value.status == "output_saved"
        self.value.status = "validating"
        self.value.validation_attempts += 1

    async def cancel_non_terminal(self, _task_id: str) -> None:
        if self.value.status not in {"succeeded", "failed", "canceled"}:
            self.value.status = "canceled"


class RecordingRuntime:
    def __init__(self, payload: bytes, incomplete_review_files: tuple[str, ...] = ()) -> None:
        self.payload = payload
        self.incomplete_review_files = incomplete_review_files
        self.calls = 0
        self.specs: list[FrozenAgentExecutionSpec] = []

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        _input_payload: bytes,
        _snapshot: object,
        _prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        self.calls += 1
        self.specs.append(execution_spec)
        return UnvalidatedAgentOutput(
            self.payload,
            ("response-1", "response-2"),
            "fake-model",
            11,
            4,
            (
                AgentResponseDiagnostic("response-1", "request-1", 6, 2, 1),
                AgentResponseDiagnostic("response-2", "request-2", 5, 2, 1),
            ),
            self.incomplete_review_files,
        )


class StreamingRuntime(RecordingRuntime):
    async def invoke_stream(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: object,
        prompt_locale: str,
        sink: object,
    ) -> UnvalidatedAgentOutput:
        emit = sink
        await emit(AgentRuntimeEvent("prompt", "complete model input", {}))
        await emit(AgentRuntimeEvent("model_started", "", {}))
        await emit(AgentRuntimeEvent("model_reasoning_delta", "Inspecting the diff", {}))
        return await self.invoke(execution_spec, input_payload, snapshot, prompt_locale)


class RecordingTranscript:
    def __init__(self) -> None:
        self.batches: list[list[tuple[str, str, object]]] = []

    async def append(
        self, _task_id: str, kind: str, content: str, *, metadata: object = None
    ) -> None:
        self.batches.append([(kind, content, metadata)])

    async def append_many(self, _task_id: str, entries: object) -> None:
        self.batches.append(list(entries))


class CancellingRuntime(RecordingRuntime):
    def __init__(self, payload: bytes, workflow: MemoryWorkflow) -> None:
        super().__init__(payload)
        self._workflow = workflow

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: object,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        output = await super().invoke(execution_spec, input_payload, snapshot, prompt_locale)
        self._workflow.is_cancellation_requested = True
        return output


class MemoryArtifacts:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    async def write_output(self, _run_id: str, payload: bytes) -> RunOutputArtifact:
        reference = f"artifact-{len(self.payloads) + 1}"
        self.payloads[reference] = payload
        return RunOutputArtifact(reference, hashlib.sha256(payload).hexdigest(), len(payload))

    async def read_output(self, reference: str, expected_hash: str) -> bytes:
        payload = self.payloads[reference]
        assert hashlib.sha256(payload).hexdigest() == expected_hash
        return payload


class EmptyValidator:
    warnings: tuple[FindingValidationWarning, ...] = ()

    async def validate(self, _payload: bytes) -> CandidateFindingBatch:
        return CandidateFindingBatch(())


class FailingValidator:
    warnings: tuple[FindingValidationWarning, ...] = ()

    async def validate(self, _payload: bytes) -> CandidateFindingBatch:
        raise ValueError("Agent output schema is invalid")


class WarningValidator:
    warnings = (
        FindingValidationWarning(1, "duplicate", "Finding duplicates an earlier candidate"),
        FindingValidationWarning(2, "invalid", "Finding references an unknown changed hunk"),
    )

    async def validate(self, _payload: bytes) -> CandidateFindingBatch:
        return CandidateFindingBatch(())


class EmptyCandidateValidator:
    warnings: tuple[FindingValidationWarning, ...] = ()

    async def validate(self, _payload: bytes) -> CandidateFindingBatch:
        return CandidateFindingBatch(())


class RecordingCompletion:
    def __init__(self, checkpoints: MemoryCheckpoints) -> None:
        self.checkpoints = checkpoints
        self.calls = 0
        self.candidate_calls = 0

    async def complete_with_candidates(
        self,
        _task_id: str,
        _node_key: str,
        _candidates: CandidateFindingBatch,
        *,
        result_summary: dict[str, object] | None = None,
    ) -> None:
        assert result_summary == {"candidate_count": 0}
        self.calls += 1
        self.candidate_calls += 1
        self.checkpoints.value.status = "succeeded"


class OneShotCrash:
    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        self.did_crash = False

    async def hit(self, boundary: str) -> None:
        if boundary == self.boundary and not self.did_crash:
            self.did_crash = True
            raise RuntimeError(f"crash:{boundary}")


def _prepared() -> PreparedReview:
    worktree = TaskWorktree("worktree-1", "review-1", "a" * 64, __file__, "b" * 40, "c" * 64)
    snapshot = ReviewSnapshot(
        "snapshot-1",
        worktree,
        ReviewTarget("a" * 40, "b" * 40, None),
        RepositoryFingerprint("b" * 40, "d" * 64, "e" * 64),
        SnapshotManifest(ReviewFileScope.include_all()),
        ChangeIndex(()),
    )
    agent = correctness_agent()
    execution_spec = CapabilityResolver.testing().resolve(
        agent=agent,
        prompt_content_hash=hashlib.sha256(agent.prompt_template.encode("utf-8")).hexdigest(),
        facts=SkillActivationFacts.empty(),
        execution_limits=AgentExecutionLimits.default(),
    )
    return PreparedReview(
        snapshot=snapshot,
        execution_specs=(execution_spec,),
        input_payloads={"correctness:v2": b"{}"},
        prompt_locale="en",
    )


def _orchestrator(
    workflow: MemoryWorkflow,
    checkpoints: MemoryCheckpoints,
    runtime: RecordingRuntime,
    artifacts: MemoryArtifacts,
    completion: RecordingCompletion,
    crash: OneShotCrash | None = None,
    transcript: RecordingTranscript | None = None,
) -> ReviewOrchestrator:
    async def prepare(_task_id: str) -> PreparedReview:
        return _prepared()

    return ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=artifacts,
        checkpoints=checkpoints,
        validator_factory=lambda *_args: EmptyValidator(),
        completion=completion,
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
        crash_injector=crash,
        transcript=transcript,
    )


async def test_non_empty_scope_cannot_complete_without_executable_nodes() -> None:
    prepared = _prepared()
    prepared = PreparedReview(
        snapshot=ReviewSnapshot(
            prepared.snapshot.snapshot_id,
            prepared.snapshot.worktree,
            prepared.snapshot.target,
            prepared.snapshot.fingerprint,
            SnapshotManifest(ReviewFileScope.include_all(("src/review.py",))),
            prepared.snapshot.change_index,
        ),
        execution_specs=(),
        input_payloads={},
        prompt_locale="en",
    )
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()

    async def prepare(_task_id: str) -> PreparedReview:
        return prepared

    orchestrator = ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=RecordingRuntime(b"{}"),
        artifacts=MemoryArtifacts(),
        checkpoints=checkpoints,
        validator_factory=lambda *_args: EmptyValidator(),
        completion=RecordingCompletion(checkpoints),
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
    )

    with pytest.raises(RuntimeError, match="no executable Agent nodes"):
        await orchestrator.execute("review-1")

    assert workflow.status == "provisioning_worktree"
    assert not workflow.job_completed


async def test_happy_path_persists_the_complete_state_sequence() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"2","candidates":[]}')
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)

    await _orchestrator(workflow, checkpoints, runtime, artifacts, completion).execute("review-1")

    assert workflow.transitions == [
        "provisioning_worktree",
        "snapshotting",
        "preparing",
        "reviewing",
        "validating",
        "synthesizing",
        "completed",
    ]
    assert checkpoints.value.status == "succeeded"
    assert runtime.calls == completion.calls == 1
    assert runtime.specs[0].agent.reference == "correctness:v2"
    assert runtime.specs[0].capability_profile.reference == "reviewer:v2"
    assert len(runtime.specs[0].fingerprint) == 64
    assert workflow.job_completed


async def test_candidate_output_uses_prepublication_completion_path() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"2","candidates":[]}')
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)

    async def prepare(_task_id: str) -> PreparedReview:
        return _prepared()

    orchestrator = ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=artifacts,
        checkpoints=checkpoints,
        validator_factory=lambda *_args: EmptyCandidateValidator(),
        completion=completion,
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
    )

    await orchestrator.execute("review-1")

    assert completion.calls == 1
    assert completion.candidate_calls == 1
    assert workflow.status == "completed"


async def test_streamed_model_events_publish_the_prompt_before_completion() -> None:
    workflow = MemoryWorkflow("reviewing")
    checkpoints = MemoryCheckpoints()
    runtime = StreamingRuntime(b'{"schema_version":"2","candidates":[]}')
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    transcript = RecordingTranscript()

    await _orchestrator(
        workflow, checkpoints, runtime, artifacts, completion, transcript=transcript
    ).execute("review-1")

    entries = [entry for batch in transcript.batches for entry in batch]
    visible = [entry[0] for entry in entries if entry[0] != "lifecycle"]
    assert visible == [
        "prompt",
        "model_started",
        "model_reasoning_delta",
        "model_output",
    ]
    model_output = next(entry for entry in entries if entry[0] == "model_output")
    assert model_output[2] == {
        "agent": "correctness:v2",
        "usage_scope": "agent_run",
            "model_name": "fake-model",
            "llm_call_count": "2",
            "checkpoint_llm_call_count": "0",
            "input_tokens": "11",
            "checkpoint_input_tokens": "0",
        "cached_input_tokens": "0",
        "cache_write_input_tokens": "0",
        "context_compaction_count": "0",
        "context_compacted_result_count": "0",
        "context_compaction_original_bytes": "0",
            "context_compaction_compressed_bytes": "0",
            "context_compaction_failure_count": "0",
        "compaction_replay_registered_count": "0",
        "compaction_replay_consumed_count": "0",
            "output_tokens": "4",
            "checkpoint_output_tokens": "0",
        "total_tokens": "15",
    }


@pytest.mark.parametrize(
    ("boundary", "expected_calls"),
    (("after_model_return", 2), ("after_output_saved", 1)),
)
async def test_restart_reuses_only_durably_checkpointed_output(
    boundary: str,
    expected_calls: int,
) -> None:
    workflow = MemoryWorkflow("reviewing")
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(json.dumps({"schema_version": "2", "candidates": []}).encode())
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    crash = OneShotCrash(boundary)
    orchestrator = _orchestrator(workflow, checkpoints, runtime, artifacts, completion, crash)

    with pytest.raises(RuntimeError, match=f"crash:{boundary}"):
        await orchestrator.execute("review-1")
    if checkpoints.value.status == "running":
        checkpoints.value.status = "pending"

    await orchestrator.execute("review-1")

    assert runtime.calls == expected_calls
    assert checkpoints.value.status == "succeeded"
    assert completion.calls == 1


async def test_cancellation_after_model_output_stops_before_validation_and_aggregation() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = CancellingRuntime(b'{"schema_version":"2","candidates":[]}', workflow)
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    aggregation_crash = OneShotCrash("before_task_aggregation")

    await _orchestrator(
        workflow,
        checkpoints,
        runtime,
        artifacts,
        completion,
        aggregation_crash,
    ).execute("review-1")

    assert workflow.status == "canceled"
    assert checkpoints.value.status == "canceled"
    assert completion.calls == 0
    assert not aggregation_crash.did_crash


async def test_finding_validation_failure_does_not_reinvoke_the_model() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"2","candidates":[]}')
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    validator = FailingValidator()

    async def prepare(_task_id: str) -> PreparedReview:
        return _prepared()

    orchestrator = ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=artifacts,
        checkpoints=checkpoints,
        validator_factory=lambda *_args: validator,
        completion=completion,
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
    )

    with pytest.raises(ValueError):
        await orchestrator.execute("review-1")

    assert runtime.calls == 1
    assert checkpoints.value.execution_attempts == 1
    assert checkpoints.value.validation_attempts == 1
    assert tuple(artifacts.payloads) == ("artifact-1",)
    assert checkpoints.value.artifact_ref == "artifact-1"
    assert completion.calls == 0


async def test_candidate_validation_warnings_complete_the_review_and_reach_transcript() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"2","candidates":[]}')
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    transcript = RecordingTranscript()

    async def prepare(_task_id: str) -> PreparedReview:
        return _prepared()

    orchestrator = ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=artifacts,
        checkpoints=checkpoints,
        validator_factory=lambda *_args: WarningValidator(),
        completion=completion,
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
        transcript=transcript,
    )

    await orchestrator.execute("review-1")

    entries = [entry for batch in transcript.batches for entry in batch]
    warning = next(entry for entry in entries if entry[2] and entry[2].get("warning_code"))
    assert warning[1] == "Finding validation retained 0 and skipped 2 model candidates"
    assert warning[2] == {
        "agent": "correctness:v2",
        "warning_code": "finding_validation_partial",
        "retained_count": "0",
        "skipped_count": "2",
        "duplicate_count": "1",
        "invalid_count": "1",
        "skipped_reasons": (
            "[duplicate] candidate#1: Finding duplicates an earlier candidate; "
            "[invalid] candidate#2: Finding references an unknown changed hunk"
        ),
    }
    assert workflow.status == "completed"
    assert completion.calls == 1


async def test_forced_completion_warns_about_files_without_verified_review_coverage() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(
        b'{"schema_version":"2","candidates":[]}',
        ("src/missed.py", "src/unread.py"),
    )
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    transcript = RecordingTranscript()

    await _orchestrator(
        workflow, checkpoints, runtime, artifacts, completion, transcript=transcript
    ).execute("review-1")

    entries = [entry for batch in transcript.batches for entry in batch]
    warning = next(
        entry
        for entry in entries
        if entry[2] and entry[2].get("warning_code") == "review_coverage_incomplete"
    )
    assert warning[1] == (
        "Review completed after the incomplete-review retry limit; "
        "2 files lack verified review coverage"
    )
    assert warning[2] == {
        "agent": "correctness:v2",
        "warning_code": "review_coverage_incomplete",
        "incomplete_file_count": "2",
        "incomplete_files": '["src/missed.py","src/unread.py"]',
    }
    assert workflow.status == "partial"
    assert workflow.transitions[-1] == "partial"
    assert completion.calls == 1


async def test_replay_before_output_saved_reinvokes_the_interrupted_model_call() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"2","candidates":[]}')
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    crash = OneShotCrash("after_model_return")

    async def prepare(_task_id: str) -> PreparedReview:
        return _prepared()

    orchestrator = ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=artifacts,
        checkpoints=checkpoints,
        validator_factory=lambda *_args: EmptyValidator(),
        completion=completion,
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
        crash_injector=crash,
    )

    with pytest.raises(RuntimeError, match="crash:after_model_return"):
        await orchestrator.execute("review-1")

    assert checkpoints.value.status == "running"
    checkpoints.value.status = "pending"

    await orchestrator.execute("review-1")

    assert runtime.calls == 2
    assert checkpoints.value.validation_attempts == 1
    assert completion.calls == 1


@dataclass(frozen=True)
class _DagRecord:
    node_key: str
    status: str


class _DagCheckpoints:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    async def ensure_plan_nodes(
        self, _plan: ReviewPlan, *, capability_fingerprints: object = None
    ) -> None:
        return None

    async def list_for_task(self, _task_id: str) -> tuple[_DagRecord, ...]:
        return tuple(_DagRecord(key, status) for key, status in self.statuses.items())


def _multi_plan() -> ReviewPlan:
    reviewer_nodes = tuple(
        ReviewPlanNode.create(
            task_id="review-1",
            node_type=ReviewPlanNodeType.REVIEWER,
            agent_reference=reference,
            pass_index=ReviewPass.REVIEWER,
            shard_id="root",
            logical_attempt_group="primary",
            depends_on=(),
        )
        for reference in ("security:v2", "performance:v2")
    )
    verifier = ReviewPlanNode.create(
        task_id="review-1",
        node_type=ReviewPlanNodeType.VERIFIER,
        agent_reference="review-verifier:v2",
        pass_index=ReviewPass.VERIFIER,
        shard_id="batch",
        logical_attempt_group="primary",
        depends_on=tuple(sorted(node.node_id for node in reviewer_nodes)),
    )
    return ReviewPlan.create(
        task_id="review-1",
        selection_mode="fixed",
        reviewer_references=("security:v2", "performance:v2"),
        nodes=(*reviewer_nodes, verifier),
        planner_reason=None,
    )


async def test_persisted_dag_waits_for_all_reviewers_then_allows_partial_team() -> None:
    plan = _multi_plan()
    reviewers = tuple(node for node in plan.nodes if node.node_type is ReviewPlanNodeType.REVIEWER)
    verifier = next(node for node in plan.nodes if node.node_type is ReviewPlanNodeType.VERIFIER)
    statuses = {node.node_id: "pending" for node in plan.nodes}
    store = _DagCheckpoints(statuses)
    scheduler = PersistedDagScheduler(plan, store)

    assert set(await scheduler.next_ready_nodes("review-1")) == set(reviewers)
    statuses[reviewers[0].node_id] = "succeeded"
    statuses[reviewers[1].node_id] = "running"
    assert await scheduler.next_ready_nodes("review-1") == ()
    statuses[reviewers[1].node_id] = "failed"
    assert await scheduler.next_ready_nodes("review-1") == (verifier,)


def test_reviewer_stage_outcome_is_derived_from_persisted_terminal_records() -> None:
    assert reviewer_stage_outcome((_DagRecord("a", "succeeded"),)) == "continue"
    assert (
        reviewer_stage_outcome((_DagRecord("a", "succeeded"), _DagRecord("b", "failed")))
        == "partial"
    )
    assert (
        reviewer_stage_outcome((_DagRecord("a", "timed_out"), _DagRecord("b", "failed")))
        == "failed"
    )


@dataclass
class _PlanCheckpoint:
    node_key: str
    status: str = "pending"
    artifact_ref: str | None = None
    artifact_hash: str | None = None
    review_completion_status: str = "complete"
    execution_attempts: int = 0


class _PlanCheckpoints:
    def __init__(self) -> None:
        self.records: dict[str, _PlanCheckpoint] = {}

    async def ensure_plan_nodes(
        self, plan: ReviewPlan, *, capability_fingerprints: object = None
    ) -> None:
        for node in plan.nodes:
            self.records.setdefault(node.node_id, _PlanCheckpoint(node.node_id))

    async def ensure(self, _task_id: str, node_key: str, _group: str) -> None:
        assert node_key in self.records

    async def list_for_task(self, _task_id: str) -> tuple[_PlanCheckpoint, ...]:
        return tuple(self.records.values())

    async def get(self, _task_id: str, node_key: str) -> _PlanCheckpoint:
        return self.records[node_key]

    async def mark_running(self, _task_id: str, node_key: str) -> None:
        record = self.records[node_key]
        assert record.status == "pending"
        record.status = "running"
        record.execution_attempts += 1

    async def mark_output_saved(
        self,
        _task_id: str,
        node_key: str,
        reference: str,
        content_hash: str,
        review_completion_status: str,
    ) -> None:
        record = self.records[node_key]
        assert record.status == "running"
        record.status = "output_saved"
        record.artifact_ref = reference
        record.artifact_hash = content_hash
        record.review_completion_status = review_completion_status

    async def mark_validating(self, _task_id: str, node_key: str) -> None:
        record = self.records[node_key]
        assert record.status == "output_saved"
        record.status = "validating"

    async def mark_failed(
        self,
        _task_id: str,
        node_key: str,
        _error_code: str,
        *,
        is_timeout: bool = False,
    ) -> None:
        self.records[node_key].status = "timed_out" if is_timeout else "failed"

    async def mark_skipped(self, _task_id: str, node_key: str, _reason_code: str) -> None:
        self.records[node_key].status = "skipped"

    async def cancel_non_terminal(self, _task_id: str) -> None:
        for record in self.records.values():
            if record.status not in {"succeeded", "failed", "skipped"}:
                record.status = "canceled"


class _PlanCompletion:
    def __init__(self, checkpoints: _PlanCheckpoints) -> None:
        self._checkpoints = checkpoints

    async def complete_with_candidates(
        self,
        _task_id: str,
        node_key: str,
        _candidates: CandidateFindingBatch,
        *,
        result_summary: dict[str, object] | None = None,
    ) -> None:
        assert result_summary == {"candidate_count": 0}
        self._checkpoints.records[node_key].status = "succeeded"


class _ScriptedRuntime:
    def __init__(self, failures: set[str]) -> None:
        self.failures = failures
        self.calls: list[str] = []

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        _input_payload: bytes,
        _snapshot: object,
        _prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        reference = execution_spec.agent.reference
        self.calls.append(reference)
        if reference in self.failures:
            raise RuntimeError("scripted provider failure")
        return UnvalidatedAgentOutput(
            b'{"schema_version":"2","candidates":[]}', (), "fake", 0, 0, ()
        )


class _LoopFailingRuntime(_ScriptedRuntime):
    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        _input_payload: bytes,
        _snapshot: object,
        _prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        self.calls.append(execution_spec.agent.reference)
        raise ToolLoopDetectedError(
            "provider payload contains token=do-not-record",
            phase="investigation",
            reason_code="identical_tool_result_loop",
        )


def _prepared_plan(plan: ReviewPlan) -> PreparedReview:
    base = _prepared()
    catalog = builtin_agent_catalog()
    resolver = CapabilityResolver.testing()
    specs_by_node = {
        node.node_id: resolver.resolve(
            agent=catalog[node.agent_reference],
            prompt_content_hash="a" * 64,
            facts=SkillActivationFacts.empty(),
            execution_limits=AgentExecutionLimits.default(),
        )
        for node in plan.nodes
    }
    return PreparedReview(
        snapshot=base.snapshot,
        execution_specs=tuple(specs_by_node.values()),
        input_payloads={node_id: b"{}" for node_id in specs_by_node},
        prompt_locale="en",
        plan=plan,
        execution_specs_by_node=specs_by_node,
    )


async def _run_multi_plan(
    failures: set[str],
) -> tuple[MemoryWorkflow, _PlanCheckpoints, _ScriptedRuntime]:
    workflow = MemoryWorkflow("preparing")
    checkpoints = _PlanCheckpoints()
    runtime = _ScriptedRuntime(failures)
    prepared = _prepared_plan(_multi_plan())

    async def prepare(_task_id: str) -> PreparedReview:
        return prepared

    orchestrator = ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=MemoryArtifacts(),
        checkpoints=checkpoints,
        validator_factory=lambda *_args: EmptyValidator(),
        completion=_PlanCompletion(checkpoints),
        agent_semaphore=asyncio.Semaphore(4),
        max_agent_runs_per_review=2,
    )
    await orchestrator.execute("review-1")
    return workflow, checkpoints, runtime


async def test_one_reviewer_failure_allows_verifier_and_keeps_sticky_partial() -> None:
    workflow, checkpoints, runtime = await _run_multi_plan({"security:v2"})
    plan = _multi_plan()

    assert workflow.status == "partial"
    assert (
        checkpoints.records[
            next(node.node_id for node in plan.nodes if node.agent_reference == "security:v2")
        ].status
        == "failed"
    )
    assert "review-verifier:v2" in runtime.calls
    verifier = next(node for node in plan.nodes if node.node_type is ReviewPlanNodeType.VERIFIER)
    assert checkpoints.records[verifier.node_id].status == "succeeded"


async def test_all_reviewer_failures_fail_task_without_running_verifier() -> None:
    workflow, checkpoints, runtime = await _run_multi_plan({"security:v2", "performance:v2"})
    plan = _multi_plan()
    verifier = next(node for node in plan.nodes if node.node_type is ReviewPlanNodeType.VERIFIER)

    assert workflow.status == "failed"
    assert "review-verifier:v2" not in runtime.calls
    assert checkpoints.records[verifier.node_id].status == "skipped"


async def test_fatal_agent_failure_records_stable_sanitized_lifecycle_event() -> None:
    reference = "general:v2"
    reviewer = ReviewPlanNode.create(
        task_id="review-1",
        node_type=ReviewPlanNodeType.REVIEWER,
        agent_reference=reference,
        pass_index=ReviewPass.REVIEWER,
        shard_id="root",
        logical_attempt_group="primary",
        depends_on=(),
    )
    plan = ReviewPlan.create(
        task_id="review-1",
        selection_mode="fixed",
        reviewer_references=(reference,),
        nodes=(reviewer,),
        planner_reason=None,
    )
    workflow = MemoryWorkflow("preparing")
    checkpoints = _PlanCheckpoints()
    transcript = RecordingTranscript()
    prepared = _prepared_plan(plan)

    async def prepare(_task_id: str) -> PreparedReview:
        return prepared

    await ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=_LoopFailingRuntime(set()),
        artifacts=MemoryArtifacts(),
        checkpoints=checkpoints,
        validator_factory=lambda *_args: EmptyValidator(),
        completion=_PlanCompletion(checkpoints),
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
        transcript=transcript,
    ).execute("review-1")

    lifecycle_entries = [
        entry
        for batch in transcript.batches
        for entry in batch
        if entry[0] == "lifecycle"
        and isinstance(entry[2], dict)
        and entry[2].get("error_type") == "ToolLoopDetectedError"
    ]
    assert len(lifecycle_entries) == 1
    _kind, content, metadata = lifecycle_entries[0]
    assert content == "Agent node failed and the persisted DAG will reduce its stage"
    assert metadata == {
        "agent": reference,
        "error_type": "ToolLoopDetectedError",
        "error_code": "tool_loop_detected",
        "reason_code": "identical_tool_result_loop",
        "phase": "investigation",
        "retryable": "false",
    }
    assert "do-not-record" not in content
    assert "do-not-record" not in json.dumps(metadata)


@pytest.mark.parametrize("reference", ("general:v2", "security:v2"))
async def test_general_or_fixed_single_reviewer_failure_fails_task(
    reference: str,
) -> None:
    reviewer = ReviewPlanNode.create(
        task_id="review-1",
        node_type=ReviewPlanNodeType.REVIEWER,
        agent_reference=reference,
        pass_index=ReviewPass.REVIEWER,
        shard_id="root",
        logical_attempt_group="primary",
        depends_on=(),
    )
    plan = ReviewPlan.create(
        task_id="review-1",
        selection_mode="fixed",
        reviewer_references=(reference,),
        nodes=(reviewer,),
        planner_reason=None,
    )
    workflow = MemoryWorkflow("preparing")
    checkpoints = _PlanCheckpoints()
    runtime = _ScriptedRuntime({reference})
    prepared = _prepared_plan(plan)

    async def prepare(_task_id: str) -> PreparedReview:
        return prepared

    await ReviewOrchestrator(
        workflow=workflow,
        prepare=prepare,
        runtime=runtime,
        artifacts=MemoryArtifacts(),
        checkpoints=checkpoints,
        validator_factory=lambda *_args: EmptyValidator(),
        completion=_PlanCompletion(checkpoints),
        agent_semaphore=asyncio.Semaphore(1),
        max_agent_runs_per_review=1,
    ).execute("review-1")

    assert workflow.status == "failed"


class _GatedPlanRuntime(_ScriptedRuntime):
    def __init__(self) -> None:
        super().__init__(set())
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active_reviewers = 0
        self.maximum_reviewers = 0

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: object,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        if execution_spec.agent.role.value == "reviewer":
            self.active_reviewers += 1
            self.maximum_reviewers = max(self.maximum_reviewers, self.active_reviewers)
            if self.active_reviewers == 2:
                self.entered.set()
            try:
                await self.release.wait()
            finally:
                self.active_reviewers -= 1
        return await super().invoke(execution_spec, input_payload, snapshot, prompt_locale)


async def test_persisted_reviewer_fanout_obeys_task_level_concurrency() -> None:
    reviewer_nodes = tuple(
        ReviewPlanNode.create(
            task_id="review-1",
            node_type=ReviewPlanNodeType.REVIEWER,
            agent_reference=reference,
            pass_index=ReviewPass.REVIEWER,
            shard_id="root",
            logical_attempt_group="primary",
            depends_on=(),
        )
        for reference in ("security:v2", "performance:v2", "architecture:v2")
    )
    verifier = ReviewPlanNode.create(
        task_id="review-1",
        node_type=ReviewPlanNodeType.VERIFIER,
        agent_reference="review-verifier:v2",
        pass_index=ReviewPass.VERIFIER,
        shard_id="batch",
        logical_attempt_group="primary",
        depends_on=tuple(sorted(node.node_id for node in reviewer_nodes)),
    )
    plan = ReviewPlan.create(
        task_id="review-1",
        selection_mode="fixed",
        reviewer_references=tuple(node.agent_reference for node in reviewer_nodes),
        nodes=(*reviewer_nodes, verifier),
        planner_reason=None,
    )

    prepared = _prepared_plan(plan)
    workflow = MemoryWorkflow("preparing")
    checkpoints = _PlanCheckpoints()
    runtime = _GatedPlanRuntime()

    async def prepare(_task_id: str) -> PreparedReview:
        return prepared

    running = asyncio.create_task(
        ReviewOrchestrator(
            workflow=workflow,
            prepare=prepare,
            runtime=runtime,
            artifacts=MemoryArtifacts(),
            checkpoints=checkpoints,
            validator_factory=lambda *_args: EmptyValidator(),
            completion=_PlanCompletion(checkpoints),
            agent_semaphore=asyncio.Semaphore(3),
            max_agent_runs_per_review=2,
        ).execute("review-1")
    )
    await asyncio.wait_for(runtime.entered.wait(), timeout=1)
    assert runtime.maximum_reviewers == 2
    runtime.release.set()
    await asyncio.wait_for(running, timeout=1)

    assert workflow.status == "completed"

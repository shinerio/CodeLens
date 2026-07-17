import asyncio
import hashlib
import json
from dataclasses import dataclass

import pytest

from codelens.findings.domain.models import FindingBatch
from codelens.review.application.orchestrator import (
    PreparedReview,
    ReviewOrchestrator,
)
from codelens.review.application.validate_findings import FindingValidationError
from codelens.review.domain.ports import RunOutputArtifact, UnvalidatedAgentOutput
from codelens.reviewer_catalog.infrastructure.builtin_agents import correctness_agent
from codelens.workspace.domain.models import (
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotManifest,
    TaskWorktree,
)


@dataclass
class MemoryCheckpoint:
    status: str = "pending"
    artifact_ref: str | None = None
    artifact_hash: str | None = None
    execution_attempts: int = 0


class MemoryWorkflow:
    def __init__(self, status: str = "created") -> None:
        self.status = status
        self.transitions: list[str] = []
        self.is_cancellation_requested = False
        self.job_completed = False

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
    ) -> None:
        assert self.value.status == "running"
        self.value.status = "output_saved"
        self.value.artifact_ref = reference
        self.value.artifact_hash = content_hash

    async def mark_validating(self, _task_id: str, _node_key: str) -> None:
        assert self.value.status == "output_saved"
        self.value.status = "validating"

    async def mark_repair_pending(self, _task_id: str, _node_key: str) -> None:
        assert self.value.status == "validating"
        self.value.status = "pending"


class RecordingRuntime:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls = 0

    async def invoke(self, _agent: object, _input_payload: bytes) -> UnvalidatedAgentOutput:
        self.calls += 1
        return UnvalidatedAgentOutput(self.payload, (), "fake", 1, 2, ())


class CancellingRuntime(RecordingRuntime):
    def __init__(self, payload: bytes, workflow: MemoryWorkflow) -> None:
        super().__init__(payload)
        self._workflow = workflow

    async def invoke(self, agent: object, input_payload: bytes) -> UnvalidatedAgentOutput:
        output = await super().invoke(agent, input_payload)
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
    async def validate(self, _payload: bytes) -> FindingBatch:
        return FindingBatch("1", ())


class RepairingValidator:
    def __init__(self) -> None:
        self.calls = 0

    async def validate(self, _payload: bytes) -> FindingBatch:
        self.calls += 1
        if self.calls == 1:
            raise FindingValidationError("Agent output schema is invalid")
        return FindingBatch("1", ())


class RecordingCompletion:
    def __init__(self, checkpoints: MemoryCheckpoints) -> None:
        self.checkpoints = checkpoints
        self.calls = 0

    async def complete_with_findings(
        self,
        _task_id: str,
        _node_key: str,
        _findings: FindingBatch,
    ) -> None:
        self.calls += 1
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
        SnapshotManifest((), (), ()),
        ChangeIndex(()),
    )
    agent = correctness_agent()
    return PreparedReview(
        snapshot=snapshot,
        agents=(agent,),
        input_payloads={"correctness:v1": b"{}"},
    )


def _orchestrator(
    workflow: MemoryWorkflow,
    checkpoints: MemoryCheckpoints,
    runtime: RecordingRuntime,
    artifacts: MemoryArtifacts,
    completion: RecordingCompletion,
    crash: OneShotCrash | None = None,
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
    )


async def test_happy_path_persists_the_complete_state_sequence() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"1","findings":[]}')
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
    assert workflow.job_completed


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
    runtime = RecordingRuntime(json.dumps({"schema_version": "1", "findings": []}).encode())
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    crash = OneShotCrash(boundary)
    orchestrator = _orchestrator(
        workflow, checkpoints, runtime, artifacts, completion, crash
    )

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
    runtime = CancellingRuntime(b'{"schema_version":"1","findings":[]}', workflow)
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
    assert checkpoints.value.status == "output_saved"
    assert completion.calls == 0
    assert not aggregation_crash.did_crash


async def test_schema_repair_is_a_second_attempt_and_preserves_first_artifact() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"1","findings":[]}')
    artifacts = MemoryArtifacts()
    completion = RecordingCompletion(checkpoints)
    validator = RepairingValidator()

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

    await orchestrator.execute("review-1")

    assert runtime.calls == 2
    assert checkpoints.value.execution_attempts == 2
    assert tuple(artifacts.payloads) == ("artifact-1", "artifact-2")
    assert checkpoints.value.artifact_ref == "artifact-2"

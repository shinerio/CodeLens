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
from codelens.review.domain.ports import (
    AgentResponseDiagnostic,
    AgentRuntimeEvent,
    FindingValidationWarning,
    RunOutputArtifact,
    UnvalidatedAgentOutput,
)
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
    validation_attempts: int = 0


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
        self.value.validation_attempts += 1


class RecordingRuntime:
    def __init__(self, payload: bytes, incomplete_review_files: tuple[str, ...] = ()) -> None:
        self.payload = payload
        self.incomplete_review_files = incomplete_review_files
        self.calls = 0

    async def invoke(
        self,
        _agent: object,
        _input_payload: bytes,
        _snapshot: object,
        _prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        self.calls += 1
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
        agent: object,
        input_payload: bytes,
        snapshot: object,
        prompt_locale: str,
        sink: object,
    ) -> UnvalidatedAgentOutput:
        emit = sink
        await emit(AgentRuntimeEvent("prompt", "complete model input", {}))
        await emit(AgentRuntimeEvent("model_started", "", {}))
        await emit(AgentRuntimeEvent("model_reasoning_delta", "Inspecting the diff", {}))
        return await self.invoke(agent, input_payload, snapshot, prompt_locale)


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
        agent: object,
        input_payload: bytes,
        snapshot: object,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        output = await super().invoke(agent, input_payload, snapshot, prompt_locale)
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

    async def validate(self, _payload: bytes) -> FindingBatch:
        return FindingBatch("1", ())


class FailingValidator:
    warnings: tuple[FindingValidationWarning, ...] = ()

    async def validate(self, _payload: bytes) -> FindingBatch:
        raise FindingValidationError("Agent output schema is invalid")


class WarningValidator:
    warnings = (
        FindingValidationWarning(1, "duplicate", "Finding duplicates an earlier candidate"),
        FindingValidationWarning(2, "invalid", "Finding references an unknown changed hunk"),
    )

    async def validate(self, _payload: bytes) -> FindingBatch:
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


async def test_streamed_model_events_publish_the_prompt_before_completion() -> None:
    workflow = MemoryWorkflow("reviewing")
    checkpoints = MemoryCheckpoints()
    runtime = StreamingRuntime(b'{"schema_version":"1","findings":[]}')
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
        "agent": "correctness:v1",
        "model_name": "fake-model",
        "llm_call_count": "2",
        "input_tokens": "11",
        "output_tokens": "4",
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


async def test_finding_validation_failure_does_not_reinvoke_the_model() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"1","findings":[]}')
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

    with pytest.raises(FindingValidationError):
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
    runtime = RecordingRuntime(b'{"schema_version":"1","findings":[]}')
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
        "agent": "correctness:v1",
        "warning_code": "finding_validation_partial",
        "retained_count": "0",
        "skipped_count": "2",
        "duplicate_count": "1",
        "invalid_count": "1",
    }
    assert workflow.status == "completed"
    assert completion.calls == 1


async def test_forced_completion_warns_about_files_without_verified_review_coverage() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(
        b'{"schema_version":"1","findings":[]}',
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
        "agent": "correctness:v1",
        "warning_code": "review_coverage_incomplete",
        "incomplete_file_count": "2",
        "incomplete_files": '["src/missed.py","src/unread.py"]',
    }
    assert workflow.status == "completed"
    assert completion.calls == 1


async def test_replay_before_output_saved_reinvokes_the_interrupted_model_call() -> None:
    workflow = MemoryWorkflow()
    checkpoints = MemoryCheckpoints()
    runtime = RecordingRuntime(b'{"schema_version":"1","findings":[]}')
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

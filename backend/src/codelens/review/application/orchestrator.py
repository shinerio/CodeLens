"""Restart-safe review workflow orchestration."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from codelens.findings.domain.models import FindingBatch
from codelens.review.domain.ports import (
    AgentReviewCompletionStatus,
    AgentRunCompletionPort,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    AgentRuntimePort,
    FindingValidationWarning,
    RunArtifactPort,
)
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.workspace.domain.models import ReviewSnapshot

type TranscriptKind = Literal[
    "lifecycle",
    "prompt",
    "model_output",
    "tool_call",
    "tool_result",
    "skill_loaded",
    "model_started",
    "model_reasoning_delta",
    "model_reasoning_completed",
    "model_output_delta",
    "model_output_completed",
    "model_completed",
    "model_raw_output",
]
type TranscriptRecord = tuple[TranscriptKind, str, Mapping[str, str] | None]


@dataclass(frozen=True)
class PreparedReview:
    """Hold one frozen Snapshot and bounded input for each immutable Agent version."""

    snapshot: ReviewSnapshot
    agents: tuple[AgentVersion, ...]
    input_payloads: dict[str, bytes]
    prompt_locale: str


class _WorkflowPort(Protocol):
    async def get_status(self, task_id: str) -> str: ...
    async def transition(self, task_id: str, status: str, **values: str) -> None: ...
    async def cancellation_requested(self, task_id: str) -> bool: ...
    async def cancel(self, task_id: str) -> None: ...
    async def fail(self, task_id: str, error_code: str) -> None: ...
    async def interrupt(self, task_id: str) -> None: ...
    async def complete_job(self, task_id: str) -> None: ...


class CheckpointView(Protocol):
    """Expose only restart decisions needed by the application orchestrator."""

    @property
    def status(self) -> str: ...

    @property
    def artifact_ref(self) -> str | None: ...

    @property
    def artifact_hash(self) -> str | None: ...

    @property
    def review_completion_status(self) -> AgentReviewCompletionStatus: ...

    @property
    def execution_attempts(self) -> int: ...


_CheckpointViewT = TypeVar("_CheckpointViewT", bound=CheckpointView, covariant=True)


class _CheckpointPort(Protocol[_CheckpointViewT]):
    async def ensure(self, task_id: str, node_key: str, group: str) -> None: ...
    async def get(self, task_id: str, node_key: str) -> _CheckpointViewT: ...
    async def mark_running(self, task_id: str, node_key: str) -> None: ...
    async def mark_output_saved(
        self,
        task_id: str,
        node_key: str,
        reference: str,
        content_hash: str,
        review_completion_status: AgentReviewCompletionStatus,
    ) -> None: ...
    async def mark_validating(self, task_id: str, node_key: str) -> None: ...


class _ValidatorPort(Protocol):
    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]: ...

    async def validate(self, payload: bytes) -> FindingBatch: ...


class _CrashInjectorPort(Protocol):
    async def hit(self, boundary: str) -> None: ...


class _TranscriptPort(Protocol):
    async def append(
        self,
        task_id: str,
        kind: TranscriptKind,
        content: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...

    async def append_many(self, task_id: str, entries: Sequence[TranscriptRecord]) -> None: ...


class _StreamingRuntimePort(Protocol):
    async def invoke_stream(
        self,
        agent: AgentVersion,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
        sink: AgentRuntimeEventSink,
    ) -> object: ...


class ReviewOrchestrator:
    """Execute one review from durable checkpoints."""

    def __init__(
        self,
        *,
        workflow: _WorkflowPort,
        prepare: Callable[[str], Awaitable[PreparedReview]],
        runtime: AgentRuntimePort,
        artifacts: RunArtifactPort,
        checkpoints: _CheckpointPort[CheckpointView],
        validator_factory: Callable[..., _ValidatorPort],
        completion: AgentRunCompletionPort,
        agent_semaphore: asyncio.Semaphore,
        max_agent_runs_per_review: int,
        transcript: _TranscriptPort | None = None,
        crash_injector: _CrashInjectorPort | None = None,
    ) -> None:
        self._workflow = workflow
        self._prepare = prepare
        self._runtime = runtime
        self._artifacts = artifacts
        self._checkpoints = checkpoints
        self._validator_factory = validator_factory
        self._completion = completion
        self._agent_semaphore = agent_semaphore
        self._review_agent_semaphore = asyncio.Semaphore(max_agent_runs_per_review)
        self._crash_injector = crash_injector
        self._transcript = transcript

    async def execute(self, task_id: str) -> None:
        """Resume one task without re-invoking nodes that have durable output."""

        try:
            await self._record(task_id, "lifecycle", "Review execution started")
            status = await self._workflow.get_status(task_id)
            if status in {"completed", "partial", "failed", "canceled"}:
                return
            status = await self._advance(task_id, status, "created", "provisioning_worktree")
            if status == "canceled":
                return
            prepared = await self._prepare(task_id)
            for expected, target in (
                ("provisioning_worktree", "snapshotting"),
                ("snapshotting", "preparing"),
                ("preparing", "reviewing"),
            ):
                status = await self._advance(task_id, status, expected, target)
                if status == "canceled":
                    return

            await asyncio.gather(
                *(self._checkpoint_output(task_id, prepared, agent) for agent in prepared.agents)
            )
            status = await self._advance(task_id, status, "reviewing", "validating")
            if status == "canceled":
                return
            await asyncio.gather(
                *(self._validate_output(task_id, prepared, agent) for agent in prepared.agents)
            )
            status = await self._advance(task_id, status, "validating", "synthesizing")
            if status == "canceled":
                return
            await self._hit("before_task_aggregation")
            completion_status = await self._task_completion_status(task_id, prepared)
            status = await self._advance(task_id, status, "synthesizing", completion_status)
            if status in {"completed", "partial"}:
                await self._workflow.complete_job(task_id)
                message = (
                    "Review execution completed"
                    if status == "completed"
                    else "Review execution completed with incomplete coverage"
                )
                await self._record(task_id, "lifecycle", message)
        except asyncio.CancelledError:
            await self._workflow.interrupt(task_id)
            raise

    async def _advance(
        self,
        task_id: str,
        status: str,
        expected: str,
        target: str,
    ) -> str:
        if status != expected:
            return status
        if await self._cancel_if_requested(task_id):
            return "canceled"
        await self._workflow.transition(task_id, target)
        await self._record(task_id, "lifecycle", f"Review phase entered: {target}")
        return target

    async def _cancel_if_requested(self, task_id: str) -> bool:
        if not await self._workflow.cancellation_requested(task_id):
            return False
        await self._workflow.cancel(task_id)
        return True

    async def _checkpoint_output(
        self,
        task_id: str,
        prepared: PreparedReview,
        agent: AgentVersion,
    ) -> None:
        if await self._cancel_if_requested(task_id):
            return
        node_key = self._node_key(agent)
        await self._checkpoints.ensure(task_id, node_key, "primary")
        checkpoint = await self._checkpoints.get(task_id, node_key)
        if checkpoint.status in {"output_saved", "validating", "succeeded"}:
            return
        if checkpoint.status != "pending":
            raise RuntimeError("interrupted checkpoint was not recovered before execution")
        input_payload = prepared.input_payloads[self._agent_key(agent)]
        transcript_records: list[TranscriptRecord] = []
        await self._checkpoints.mark_running(task_id, node_key)
        await self._hit("before_model_invocation")
        last_transcript_flush = time.monotonic()

        async def record_stream_event(event: AgentRuntimeEvent) -> None:
            nonlocal last_transcript_flush
            await self._buffer_runtime_event(transcript_records, agent, event)
            if time.monotonic() - last_transcript_flush >= 1.0:
                await self._record_many(task_id, transcript_records)
                transcript_records.clear()
                last_transcript_flush = time.monotonic()

        async with self._review_agent_semaphore:
            async with self._agent_semaphore:
                stream = getattr(self._runtime, "invoke_stream", None)
                if stream is None:
                    output = await self._runtime.invoke(
                        agent,
                        input_payload,
                        prepared.snapshot,
                        prepared.prompt_locale,
                    )
                else:
                    output = await stream(
                        agent,
                        input_payload,
                        prepared.snapshot,
                        prepared.prompt_locale,
                        record_stream_event,
                    )
        transcript_records.append(
            (
                "model_output",
                output.canonical_bytes.decode("utf-8", errors="replace"),
                {
                    "agent": self._agent_key(agent),
                    "model_name": output.model_name,
                    "llm_call_count": str(len(output.diagnostics)),
                    "input_tokens": str(output.input_tokens),
                    "output_tokens": str(output.output_tokens),
                    "total_tokens": str(output.input_tokens + output.output_tokens),
                },
            )
        )
        if output.incomplete_review_files:
            incomplete_files = json.dumps(
                output.incomplete_review_files,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            transcript_records.append(
                (
                    "lifecycle",
                    (
                        "Review completed after the incomplete-review retry limit; "
                        f"{len(output.incomplete_review_files)} files lack verified review coverage"
                    ),
                    {
                        "agent": self._agent_key(agent),
                        "warning_code": "review_coverage_incomplete",
                        "incomplete_file_count": str(len(output.incomplete_review_files)),
                        "incomplete_files": incomplete_files,
                    },
                )
            )
        await self._record_many(task_id, transcript_records)
        await self._hit("after_model_return")
        artifact = await self._artifacts.write_output(node_key, output.canonical_bytes)
        await self._hit("after_artifact_write")
        await self._checkpoints.mark_output_saved(
            task_id,
            node_key,
            artifact.reference,
            artifact.content_hash,
            output.review_completion_status,
        )
        await self._hit("after_output_saved")

    async def _validate_output(
        self,
        task_id: str,
        prepared: PreparedReview,
        agent: AgentVersion,
    ) -> None:
        if await self._cancel_if_requested(task_id):
            return
        node_key = self._node_key(agent)
        checkpoint = await self._checkpoints.get(task_id, node_key)
        if checkpoint.status == "succeeded":
            return
        if checkpoint.status == "output_saved":
            await self._checkpoints.mark_validating(task_id, node_key)
            checkpoint = await self._checkpoints.get(task_id, node_key)
        if (
            checkpoint.status != "validating"
            or checkpoint.artifact_ref is None
            or checkpoint.artifact_hash is None
        ):
            raise RuntimeError("checkpoint has no durable output to validate")
        payload = await self._artifacts.read_output(
            checkpoint.artifact_ref,
            checkpoint.artifact_hash,
        )
        validator = self._validator_factory(task_id, node_key, prepared, agent)
        findings = await validator.validate(payload)
        if validator.warnings:
            duplicate_count = sum(
                warning.reason_code == "duplicate" for warning in validator.warnings
            )
            invalid_count = len(validator.warnings) - duplicate_count
            await self._record(
                task_id,
                "lifecycle",
                (
                    f"Finding validation retained {len(findings.findings)} and skipped "
                    f"{len(validator.warnings)} model candidates"
                ),
                {
                    "agent": self._agent_key(agent),
                    "warning_code": "finding_validation_partial",
                    "retained_count": str(len(findings.findings)),
                    "skipped_count": str(len(validator.warnings)),
                    "duplicate_count": str(duplicate_count),
                    "invalid_count": str(invalid_count),
                },
            )
        await self._completion.complete_with_findings(task_id, node_key, findings)
        await self._hit("after_finding_completion")

    async def _task_completion_status(
        self,
        task_id: str,
        prepared: PreparedReview,
    ) -> Literal["completed", "partial"]:
        """Return PARTIAL when any durable Agent output has incomplete Review coverage."""

        checkpoints = await asyncio.gather(
            *(
                self._checkpoints.get(task_id, self._node_key(agent))
                for agent in prepared.agents
            )
        )
        return (
            "partial"
            if any(item.review_completion_status == "incomplete" for item in checkpoints)
            else "completed"
        )

    async def _hit(self, boundary: str) -> None:
        if self._crash_injector is not None:
            await self._crash_injector.hit(boundary)

    async def _record(
        self,
        task_id: str,
        kind: TranscriptKind,
        content: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        if self._transcript is not None:
            await self._transcript.append(task_id, kind, content, metadata=metadata)

    async def _record_runtime_event(
        self,
        records: list[TranscriptRecord],
        agent: AgentVersion,
        event: AgentRuntimeEvent,
    ) -> None:
        """Keep streamed chunks in memory until the model produces complete output."""

        records.append(
            (event.kind, event.content, {"agent": self._agent_key(agent), **event.metadata})
        )

    _buffer_runtime_event = _record_runtime_event

    async def _record_many(self, task_id: str, records: Sequence[TranscriptRecord]) -> None:
        if self._transcript is not None:
            await self._transcript.append_many(task_id, records)

    @staticmethod
    def _agent_key(agent: AgentVersion) -> str:
        return f"{agent.agent_id}:v{agent.version}"

    @classmethod
    def _node_key(cls, agent: AgentVersion) -> str:
        return f"{cls._agent_key(agent)}:0:root"

"""Restart-safe review workflow orchestration."""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from codelens.findings.domain.models import FindingBatch
from codelens.review.application.validate_findings import FindingValidationError
from codelens.review.domain.ports import (
    AgentRunCompletionPort,
    AgentRuntimePort,
    RunArtifactPort,
)
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.workspace.domain.models import ReviewSnapshot


@dataclass(frozen=True)
class PreparedReview:
    """Hold one frozen Snapshot and bounded input for each immutable Agent version."""

    snapshot: ReviewSnapshot
    agents: tuple[AgentVersion, ...]
    input_payloads: dict[str, bytes]


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
    def execution_attempts(self) -> int: ...

    @property
    def validation_attempts(self) -> int: ...


_CheckpointViewT = TypeVar("_CheckpointViewT", bound=CheckpointView, covariant=True)


class _CheckpointPort(Protocol[_CheckpointViewT]):
    async def ensure(self, task_id: str, node_key: str, group: str) -> None: ...
    async def get(self, task_id: str, node_key: str) -> _CheckpointViewT: ...
    async def mark_running(self, task_id: str, node_key: str) -> None: ...
    async def mark_output_saved(
        self, task_id: str, node_key: str, reference: str, content_hash: str
    ) -> None: ...
    async def mark_validating(self, task_id: str, node_key: str) -> None: ...
    async def mark_repair_pending(self, task_id: str, node_key: str) -> None: ...


class _ValidatorPort(Protocol):
    async def validate(self, payload: bytes) -> FindingBatch: ...


class _CrashInjectorPort(Protocol):
    async def hit(self, boundary: str) -> None: ...


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

    async def execute(self, task_id: str) -> None:
        """Resume one task without re-invoking nodes that have durable output."""

        try:
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
            status = await self._advance(task_id, status, "synthesizing", "completed")
            if status == "completed":
                await self._workflow.complete_job(task_id)
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
        if checkpoint.artifact_ref is not None and checkpoint.artifact_hash is not None:
            invalid_output = await self._artifacts.read_output(
                checkpoint.artifact_ref,
                checkpoint.artifact_hash,
            )
            input_payload = self._repair_payload(input_payload, invalid_output)
        await self._checkpoints.mark_running(task_id, node_key)
        await self._hit("before_model_invocation")
        async with self._review_agent_semaphore:
            async with self._agent_semaphore:
                output = await self._runtime.invoke(agent, input_payload)
        await self._hit("after_model_return")
        artifact = await self._artifacts.write_output(node_key, output.canonical_bytes)
        await self._hit("after_artifact_write")
        await self._checkpoints.mark_output_saved(
            task_id,
            node_key,
            artifact.reference,
            artifact.content_hash,
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
        try:
            findings = await validator.validate(payload)
        except FindingValidationError:
            if checkpoint.validation_attempts >= 2:
                raise
            await self._checkpoints.mark_repair_pending(task_id, node_key)
            await self._checkpoint_output(task_id, prepared, agent)
            await self._validate_output(task_id, prepared, agent)
            return
        await self._completion.complete_with_findings(task_id, node_key, findings)
        await self._hit("after_finding_completion")

    async def _hit(self, boundary: str) -> None:
        if self._crash_injector is not None:
            await self._crash_injector.hit(boundary)

    @staticmethod
    def _agent_key(agent: AgentVersion) -> str:
        return f"{agent.agent_id}:v{agent.version}"

    @classmethod
    def _node_key(cls, agent: AgentVersion) -> str:
        return f"{cls._agent_key(agent)}:0:root"

    @staticmethod
    def _repair_payload(review_input: bytes, invalid_output: bytes) -> bytes:
        """Create an explicit repair attempt without mutating the first Artifact."""

        return json.dumps(
            {
                "instruction": "Return a corrected FindingBatch matching the declared schema.",
                "invalid_output_base64": base64.b64encode(invalid_output).decode("ascii"),
                "review_input_base64": base64.b64encode(review_input).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

"""Restart-safe review workflow orchestration."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar

from codelens.capabilities.domain.models import FrozenAgentExecutionSpec
from codelens.findings.domain.candidates import CandidateFindingBatch
from codelens.findings.infrastructure.verdict_codec import ValidatedVerdictBatch
from codelens.review.application.dag_scheduler import (
    PersistedDagScheduler,
    reviewer_stage_outcome,
)
from codelens.review.domain.ports import (
    AgentReviewCompletionStatus,
    AgentRunCompletionPort,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    AgentRuntimePort,
    FindingValidationWarning,
    RunArtifactPort,
)
from codelens.review.domain.review_plan import ReviewPlan, ReviewPlanNode, ReviewPlanNodeType
from codelens.workspace.domain.models import ReviewSnapshot

type TranscriptKind = Literal[
    "lifecycle",
    "prompt",
    "model_output",
    "tool_call",
    "invalid_tool_call",
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
    """Hold one Snapshot and bounded input for each frozen Agent execution spec."""

    snapshot: ReviewSnapshot
    execution_specs: tuple[FrozenAgentExecutionSpec, ...]
    input_payloads: dict[str, bytes]
    prompt_locale: str
    plan: ReviewPlan | None = None
    execution_specs_by_node: dict[str, FrozenAgentExecutionSpec] = field(default_factory=dict)


class _WorkflowPort(Protocol):
    async def get_status(self, task_id: str) -> str: ...
    async def transition(self, task_id: str, status: str, **values: str) -> None: ...
    async def cancellation_requested(self, task_id: str) -> bool: ...
    async def cancel(self, task_id: str) -> None: ...
    async def fail(self, task_id: str, error_code: str) -> None: ...
    async def interrupt(self, task_id: str) -> None: ...
    async def complete_job(self, task_id: str) -> None: ...
    async def mark_partial_coverage(self, task_id: str) -> None: ...
    async def has_partial_coverage(self, task_id: str) -> bool: ...


class CheckpointView(Protocol):
    """Expose only restart decisions needed by the application orchestrator."""

    @property
    def node_key(self) -> str: ...

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

    @property
    def run_id(self) -> str | None: ...


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
    async def ensure_plan_nodes(
        self,
        plan: ReviewPlan,
        *,
        capability_fingerprints: dict[str, str] | None = None,
    ) -> None: ...
    async def list_for_task(self, task_id: str) -> tuple[_CheckpointViewT, ...]: ...
    async def mark_failed(
        self, task_id: str, node_key: str, error_code: str, *, is_timeout: bool = False
    ) -> None: ...
    async def mark_skipped(self, task_id: str, node_key: str, reason_code: str) -> None: ...
    async def cancel_non_terminal(self, task_id: str) -> None: ...


class _ValidatorPort(Protocol):
    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]: ...

    async def validate(self, payload: bytes) -> CandidateFindingBatch | ValidatedVerdictBatch: ...


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
        execution_spec: FrozenAgentExecutionSpec,
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
        prepare_verdict: Callable[[str, PreparedReview], Awaitable[None]] | None = None,
        publish_findings: Callable[[str], Awaitable[None]] | None = None,
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
        self._prepare_verdict = prepare_verdict
        self._publish_findings = publish_findings
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
            if (
                prepared.snapshot.manifest.review_paths
                and prepared.plan is None
                and not prepared.execution_specs
            ):
                raise RuntimeError("non-empty Review scope produced no executable Agent nodes")
            for expected, target in (
                ("provisioning_worktree", "snapshotting"),
                ("snapshotting", "preparing"),
            ):
                status = await self._advance(task_id, status, expected, target)
                if status == "canceled":
                    return

            if prepared.plan is not None:
                status = await self._advance(task_id, status, "preparing", "planning")
                if status == "canceled":
                    return
                status = await self._advance(task_id, status, "planning", "reviewing")
                if status == "canceled":
                    return
                await self._execute_persisted_plan(task_id, status, prepared)
                return

            status = await self._advance(task_id, status, "preparing", "reviewing")
            if status == "canceled":
                return

            results = await asyncio.gather(
                *(
                    self._checkpoint_output(task_id, prepared, execution_spec)
                    for execution_spec in prepared.execution_specs
                ),
                return_exceptions=True,
            )
            exceptions = [r for r in results if isinstance(r, BaseException)]
            if exceptions:
                non_cancelled = [e for e in exceptions if not isinstance(e, asyncio.CancelledError)]
                raise non_cancelled[0] if non_cancelled else exceptions[0]
            status = await self._advance(task_id, status, "reviewing", "validating")
            if status == "canceled":
                return
            results = await asyncio.gather(
                *(
                    self._validate_output(task_id, prepared, execution_spec)
                    for execution_spec in prepared.execution_specs
                ),
                return_exceptions=True,
            )
            exceptions = [r for r in results if isinstance(r, BaseException)]
            if exceptions:
                non_cancelled = [e for e in exceptions if not isinstance(e, asyncio.CancelledError)]
                raise non_cancelled[0] if non_cancelled else exceptions[0]
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
            if await self._workflow.cancellation_requested(task_id):
                await self._checkpoints.cancel_non_terminal(task_id)
                await self._workflow.cancel(task_id)
            else:
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
        await self._checkpoints.cancel_non_terminal(task_id)
        await self._workflow.cancel(task_id)
        return True

    async def _execute_persisted_plan(
        self, task_id: str, status: str, prepared: PreparedReview
    ) -> None:
        """Drive one frozen DAG from durable node state with isolated failures."""

        plan = prepared.plan
        if plan is None:
            raise RuntimeError("persisted execution requires a Review Plan")
        scheduler = PersistedDagScheduler(plan, self._checkpoints)
        specs = prepared.execution_specs_by_node
        if set(specs) != {node.node_id for node in plan.nodes}:
            raise ValueError("prepared execution specs do not match the Review Plan")
        await scheduler.initialize({node_id: spec.fingerprint for node_id, spec in specs.items()})
        planner_nodes = tuple(
            node for node in plan.nodes if node.node_type is ReviewPlanNodeType.PLANNER
        )
        if planner_nodes:
            planner_checkpoint = await self._checkpoints.get(task_id, planner_nodes[0].node_id)
            if planner_checkpoint.status != "succeeded":
                raise RuntimeError("Adaptive Planner output must be durable before Plan execution")

        while True:
            if await self._cancel_if_requested(task_id):
                return
            records = await self._checkpoints.list_for_task(task_id)
            by_node = {record.node_key: record for record in records}
            reviewer_nodes = tuple(
                node for node in plan.nodes if node.node_type is ReviewPlanNodeType.REVIEWER
            )
            reviewer_records = tuple(by_node[node.node_id] for node in reviewer_nodes)
            reviewers_terminal = all(
                record.status in {"succeeded", "failed", "timed_out", "canceled", "skipped"}
                for record in reviewer_records
            )
            if reviewers_terminal:
                outcome = reviewer_stage_outcome(reviewer_records)
                if outcome == "failed":
                    await self._skip_pending_nodes(task_id, plan, by_node, "reviewer_stage_failed")
                    await self._workflow.fail(task_id, "all_reviewers_failed")
                    return
                if outcome == "partial" or any(
                    record.review_completion_status == "incomplete"
                    for record in reviewer_records
                    if record.status == "succeeded"
                ):
                    await self._workflow.mark_partial_coverage(task_id)

            verifier = next(
                (node for node in plan.nodes if node.node_type is ReviewPlanNodeType.VERIFIER),
                None,
            )
            if verifier is not None and reviewers_terminal:
                if self._prepare_verdict is not None:
                    await self._prepare_verdict(task_id, prepared)
                verifier_status = by_node[verifier.node_id].status
                if verifier_status in {"failed", "timed_out"}:
                    await self._workflow.mark_partial_coverage(task_id)
                    if self._publish_findings is not None:
                        await self._publish_findings(task_id)
                    await self._finish_persisted_task(task_id, status)
                    return
                if verifier_status == "succeeded":
                    if self._publish_findings is not None:
                        await self._publish_findings(task_id)
                    await self._finish_persisted_task(task_id, status)
                    return
            elif verifier is None and reviewers_terminal:
                if self._prepare_verdict is not None:
                    await self._prepare_verdict(task_id, prepared)
                if self._publish_findings is not None:
                    await self._publish_findings(task_id)
                await self._finish_persisted_task(task_id, status)
                return

            ready = await scheduler.next_ready_nodes(task_id)
            ready = tuple(
                node for node in ready if node.node_type is not ReviewPlanNodeType.PLANNER
            )
            if not ready:
                raise RuntimeError("persisted Review DAG has no ready or terminal reduction")
            if any(node.node_type is ReviewPlanNodeType.VERIFIER for node in ready):
                status = await self._advance(task_id, status, "reviewing", "verifying")
            await asyncio.gather(
                *(self._execute_plan_node(task_id, prepared, node) for node in ready)
            )

    async def _execute_plan_node(
        self, task_id: str, prepared: PreparedReview, node: ReviewPlanNode
    ) -> None:
        execution_spec = prepared.execution_specs_by_node[node.node_id]
        try:
            await self._checkpoint_output(task_id, prepared, execution_spec, node_key=node.node_id)
            await self._validate_output(task_id, prepared, execution_spec, node_key=node.node_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            checkpoint = await self._checkpoints.get(task_id, node.node_id)
            if checkpoint.status in {"running", "validating"}:
                await self._checkpoints.mark_failed(
                    task_id,
                    node.node_id,
                    "agent_node_failed",
                    is_timeout=isinstance(error, TimeoutError),
                )
            await self._record(
                task_id,
                "lifecycle",
                "Agent node failed and the persisted DAG will reduce its stage",
                {
                    "agent": node.agent_reference,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )

    async def _skip_pending_nodes(
        self,
        task_id: str,
        plan: ReviewPlan,
        records: Mapping[str, CheckpointView],
        reason_code: str,
    ) -> None:
        for node in plan.nodes:
            if records[node.node_id].status == "pending":
                await self._checkpoints.mark_skipped(task_id, node.node_id, reason_code)

    async def _finish_persisted_task(self, task_id: str, status: str) -> None:
        target = "partial" if await self._workflow.has_partial_coverage(task_id) else "completed"
        final_status = await self._advance(task_id, status, status, target)
        if final_status in {"completed", "partial"}:
            await self._workflow.complete_job(task_id)
            await self._record(
                task_id,
                "lifecycle",
                (
                    "Review execution completed"
                    if final_status == "completed"
                    else "Review execution completed with partial Agent coverage"
                ),
            )

    async def _checkpoint_output(
        self,
        task_id: str,
        prepared: PreparedReview,
        execution_spec: FrozenAgentExecutionSpec,
        *,
        node_key: str | None = None,
    ) -> None:
        if await self._cancel_if_requested(task_id):
            return
        node_key = node_key or self._node_key(execution_spec)
        await self._checkpoints.ensure(task_id, node_key, "primary")
        checkpoint = await self._checkpoints.get(task_id, node_key)
        if checkpoint.status in {"output_saved", "validating", "succeeded"}:
            return
        if checkpoint.status != "pending":
            raise RuntimeError("interrupted checkpoint was not recovered before execution")
        input_payload = (
            prepared.input_payloads[node_key]
            if node_key in prepared.input_payloads
            else prepared.input_payloads[self._agent_key(execution_spec)]
        )
        transcript_records: list[TranscriptRecord] = []
        await self._checkpoints.mark_running(task_id, node_key)
        await self._hit("before_model_invocation")
        last_transcript_flush = time.monotonic()

        async def record_stream_event(event: AgentRuntimeEvent) -> None:
            nonlocal last_transcript_flush
            await self._buffer_runtime_event(transcript_records, execution_spec, event)
            if time.monotonic() - last_transcript_flush >= 1.0:
                await self._record_many(task_id, transcript_records)
                transcript_records.clear()
                last_transcript_flush = time.monotonic()

        async with self._review_agent_semaphore:
            async with self._agent_semaphore:
                stream = getattr(self._runtime, "invoke_stream", None)
                if stream is None:
                    output = await self._runtime.invoke(
                        execution_spec,
                        input_payload,
                        prepared.snapshot,
                        prepared.prompt_locale,
                    )
                else:
                    output = await stream(
                        execution_spec,
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
                    "agent": self._agent_key(execution_spec),
                    "usage_scope": "agent_run",
                    "model_name": output.model_name,
                    "llm_call_count": str(len(output.diagnostics)),
                    "input_tokens": str(output.input_tokens),
                    "cached_input_tokens": str(output.cached_input_tokens),
                    "cache_write_input_tokens": str(output.cache_write_input_tokens),
                    "context_compaction_count": str(output.context_compaction_count),
                    "context_compacted_result_count": str(
                        output.context_compacted_result_count
                    ),
                    "context_compaction_original_bytes": str(
                        output.context_compaction_original_bytes
                    ),
                    "context_compaction_compressed_bytes": str(
                        output.context_compaction_compressed_bytes
                    ),
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
                        "agent": self._agent_key(execution_spec),
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
        execution_spec: FrozenAgentExecutionSpec,
        *,
        node_key: str | None = None,
    ) -> None:
        if await self._cancel_if_requested(task_id):
            return
        node_key = node_key or self._node_key(execution_spec)
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
        validator = self._validator_factory(
            task_id,
            node_key,
            prepared,
            execution_spec.agent,
            checkpoint,
        )
        validated = await validator.validate(payload)
        if validator.warnings:
            retained_count = (
                len(validated.candidates)
                if isinstance(validated, CandidateFindingBatch)
                else len(validated.decisions)
            )
            duplicate_count = sum(
                warning.reason_code == "duplicate" for warning in validator.warnings
            )
            invalid_count = len(validator.warnings) - duplicate_count
            skipped_reasons = "; ".join(
                f"[{warning.reason_code}] candidate#{warning.candidate_index}: {warning.message}"
                for warning in validator.warnings
            )
            await self._record(
                task_id,
                "lifecycle",
                (
                    f"Finding validation retained {retained_count} and skipped "
                    f"{len(validator.warnings)} model candidates"
                ),
                {
                    "agent": self._agent_key(execution_spec),
                    "warning_code": "finding_validation_partial",
                    "retained_count": str(retained_count),
                    "skipped_count": str(len(validator.warnings)),
                    "duplicate_count": str(duplicate_count),
                    "invalid_count": str(invalid_count),
                    "skipped_reasons": skipped_reasons,
                },
            )
        if isinstance(validated, ValidatedVerdictBatch):
            await self._completion.complete_with_verdicts(task_id, node_key, validated.decisions)
        elif isinstance(validated, CandidateFindingBatch):
            await self._completion.complete_with_candidates(
                task_id,
                node_key,
                validated,
                result_summary={"candidate_count": len(validated.candidates)},
            )
        await self._hit("after_candidate_completion")

    async def _task_completion_status(
        self,
        task_id: str,
        prepared: PreparedReview,
    ) -> Literal["completed", "partial"]:
        """Return PARTIAL when any durable Agent output has incomplete Review coverage."""

        checkpoints = await asyncio.gather(
            *(
                self._checkpoints.get(task_id, self._node_key(execution_spec))
                for execution_spec in prepared.execution_specs
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
        execution_spec: FrozenAgentExecutionSpec,
        event: AgentRuntimeEvent,
    ) -> None:
        """Keep streamed chunks in memory until the model produces complete output."""

        records.append(
            (
                event.kind,
                event.content,
                {"agent": self._agent_key(execution_spec), **event.metadata},
            )
        )

    _buffer_runtime_event = _record_runtime_event

    async def _record_many(self, task_id: str, records: Sequence[TranscriptRecord]) -> None:
        if self._transcript is not None:
            await self._transcript.append_many(task_id, records)

    @staticmethod
    def _agent_key(execution_spec: FrozenAgentExecutionSpec) -> str:
        return execution_spec.agent.reference

    @classmethod
    def _node_key(cls, execution_spec: FrozenAgentExecutionSpec) -> str:
        return f"{cls._agent_key(execution_spec)}:0:root"

"""Worker-side reconstruction and execution of durable review commands."""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import cast

from codelens.bootstrap.settings import Settings
from codelens.bootstrap.web_settings_defaults import load_web_settings_defaults
from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    FrozenAgentExecutionSpec,
    hydrate_execution_spec,
)
from codelens.capabilities.domain.skills import SkillActivationFacts
from codelens.capabilities.infrastructure.builtin_profiles import (
    builtin_capability_profiles,
    builtin_skill_policies,
)
from codelens.findings.application.publish_findings import FindingPublisher
from codelens.findings.application.resolve_clusters import ClusterService, publish_all_verdicts
from codelens.findings.application.validate_candidates import CandidateValidator
from codelens.findings.domain.candidates import CandidateFinding
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.dedup import (
    SurvivedFinding,
    run_deterministic_filter,
)
from codelens.findings.domain.existing_findings import ExistingFindingSet
from codelens.findings.domain.remediation import (
    PendingRemediation,
    run_deterministic_remediation_filter,
)
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome, verdict_decision_id
from codelens.findings.infrastructure.dedup_codec import DedupCodec
from codelens.findings.infrastructure.remediation_codec import RemediationCodec
from codelens.findings.infrastructure.verdict_codec import VerdictCodec
from codelens.instruction_policy.domain.models import ResolvedInstructionSet
from codelens.review.application.context_builder import ContextBuilder
from codelens.review.application.orchestrator import (
    CheckpointView,
    PreparedReview,
    ReviewOrchestrator,
)
from codelens.review.application.planning import (
    MANDATORY_ADAPTIVE_REVIEWERS,
    CapabilityReadiness,
    ChangeRiskSummary,
    PlannerSelection,
    ReviewPlanCompiler,
    ReviewPlanningService,
    build_planner_input_payload,
)
from codelens.review.application.tool_limits_service import ToolLimitsService
from codelens.review.domain.agent_run import AgentRun, InvalidAgentRunStateError
from codelens.review.domain.errors import AgentRuntimeError
from codelens.review.domain.ports import (
    AgentReviewCompletionStatus,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    AgentRuntimePort,
    ArtifactIdentity,
    ReviewExecutionRecord,
    SnapshotFileReaderPort,
    UnvalidatedAgentOutput,
)
from codelens.review.domain.review_plan import ReviewPlan, ReviewPlanNode, ReviewPlanNodeType
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    FixedReviewerSelection,
)
from codelens.review.infrastructure.dedup_tools import DedupValidator
from codelens.review.infrastructure.file_tool_limits import FilesystemToolLimitsStore
from codelens.review.infrastructure.planner_output import PlannerOutputCodec
from codelens.review.infrastructure.remediation_tools import RemediationValidator
from codelens.review.infrastructure.repositories import (
    CheckpointRecord,
    SqlAgentExecutionSpecStore,
    SqlCandidateFindingStore,
    SqlCheckpointStore,
    SqlJobQueue,
    SqlReviewPlanStore,
    SqlReviewStore,
    SqlVerdictStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.review.infrastructure.transcripts import WorkerTranscriptStore
from codelens.review.infrastructure.verdict_tools import VerdictValidator
from codelens.reviewer_catalog.application.prompt_settings import AgentPromptSettingsService
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.domain.provider_config import (
    ModelProviderConfig,
    ModelProviderConfigPort,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemAgentPromptStore,
)
from codelens.reviewer_catalog.infrastructure.file_provider_config import (
    FilesystemModelProviderConfigAdapter,
)
from codelens.shared.domain.errors import DomainError
from codelens.worker.scheduler import (
    ClaimedJob,
    WorkerSemaphores,
    fair_per_review_agent_limit,
)
from codelens.workspace.application.create_snapshot import SnapshotService
from codelens.workspace.application.inspect_repository import RepositoryInspector
from codelens.workspace.application.worktree_lifecycle import (
    ReviewWorktreeLifecycle,
    ReviewWorktreeRecoveryService,
    WorktreeRecoveryInput,
)
from codelens.workspace.domain.models import (
    CapturedReviewInput,
    OpaqueArtifact,
    ReviewSnapshot,
    ReviewTarget,
)
from codelens.workspace.domain.ports import ScopePlan
from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter

_TERMINAL_STATUSES = {"completed", "partial", "failed", "canceled"}


class _RejectAdaptivePlanning:
    """Guard the Fixed-only Worker path against an accidental Planner invocation."""

    async def select(
        self,
        *,
        task_id: str,
        candidate_paths: tuple[str, ...],
        readiness: Mapping[str, CapabilityReadiness],
        risk_summary: ChangeRiskSummary | None,
    ) -> PlannerSelection:
        raise RuntimeError("Fixed Review Plan must not invoke the Planner")


class _SelectedAdaptivePlanning:
    """Return the already validated, durable Planner selection exactly once."""

    def __init__(self, selection: PlannerSelection) -> None:
        self._selection = selection

    async def select(
        self,
        *,
        task_id: str,
        candidate_paths: tuple[str, ...],
        readiness: Mapping[str, CapabilityReadiness],
        risk_summary: ChangeRiskSummary | None,
    ) -> PlannerSelection:
        del task_id, candidate_paths, readiness, risk_summary
        return self._selection


async def load_frozen_execution_specs(
    task_id: str,
    store: SqlAgentExecutionSpecStore,
    artifacts: FilesystemRunArtifactStore,
) -> dict[str, FrozenAgentExecutionSpec]:
    """Hydrate task specs exclusively from hash-verified frozen Artifact bytes."""

    hydrated: dict[str, FrozenAgentExecutionSpec] = {}
    for record in await store.list_for_task(task_id):
        prompt_bytes = await artifacts.read_output(
            record.prompt_artifact_ref, record.prompt_artifact_hash
        )
        loaded_skill_bytes: list[bytes] = []
        for identity in record.skill_artifacts:
            loaded_skill_bytes.append(
                await artifacts.read_output(identity.reference, identity.content_hash)
            )
        skill_bytes = tuple(loaded_skill_bytes)
        try:
            execution_spec = hydrate_execution_spec(
                record.spec_json,
                prompt_text=prompt_bytes.decode("utf-8"),
                skill_instruction_texts=tuple(payload.decode("utf-8") for payload in skill_bytes),
            )
        except UnicodeDecodeError as error:
            raise ValueError("frozen execution Artifact is not valid UTF-8") from error
        if execution_spec.fingerprint != record.fingerprint:
            raise ValueError("hydrated execution spec fingerprint mismatch")
        hydrated[record.logical_node_id] = execution_spec
    return hydrated


def add_reviewer_plan_guidance(
    base_payload: bytes,
    *,
    reason_codes: tuple[str, ...],
    focus_paths: tuple[str, ...],
) -> bytes:
    """Add bounded Planner attention hints without changing Snapshot evidence scope."""

    try:
        envelope = json.loads(base_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Reviewer input is not canonical JSON") from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "review_files",
        "repository_instructions",
    }:
        raise ValueError("Reviewer input has an invalid shape")
    envelope["role_context"] = {
        "planner_guidance": {
            "focus_paths": list(focus_paths),
            "reason_codes": list(reason_codes),
        }
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()


def add_host_run_identity(input_payload: bytes, run_id: str) -> bytes:
    """Attach trusted execution identity that the runtime strips from model input."""

    try:
        envelope = json.loads(input_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Agent input is not canonical JSON") from error
    if not isinstance(envelope, dict):
        raise ValueError("Agent input has an invalid shape")
    role_context = envelope.setdefault("role_context", {})
    if not isinstance(role_context, dict):
        raise ValueError("Agent role context must be an object")
    role_context["_host_run_id"] = run_id
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()


def add_existing_findings_context(
    base_payload: bytes, existing_findings: ExistingFindingSet
) -> bytes:
    """Attach frozen duplicate context without accepting arbitrary prompt fragments."""

    if not existing_findings.items:
        return base_payload
    try:
        envelope = json.loads(base_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Agent input is not canonical JSON") from error
    if not isinstance(envelope, dict):
        raise ValueError("Agent input has an invalid shape")
    role_context = envelope.setdefault("role_context", {})
    if not isinstance(role_context, dict):
        raise ValueError("Agent role context must be an object")
    if "existing_findings" in role_context:
        raise ValueError("Agent input already contains existing findings")
    role_context["existing_findings"] = {
        "schema_version": "1",
        "findings": [finding.as_payload() for finding in existing_findings.items],
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class _FailureDiagnostic:
    """Carry one credential-safe user diagnostic into the durable transcript."""

    content: str
    metadata: dict[str, str]


class _ModelLimitedRuntime:
    """Apply the Worker-wide model semaphore around one provider invocation."""

    def __init__(self, runtime: AgentRuntimePort, semaphore: asyncio.Semaphore) -> None:
        self._runtime = runtime
        self._semaphore = semaphore

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        async with self._semaphore:
            return await self._runtime.invoke(
                execution_spec,
                input_payload,
                snapshot,
                prompt_locale,
            )

    async def invoke_stream(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
        sink: AgentRuntimeEventSink,
    ) -> UnvalidatedAgentOutput:
        """Keep streamed provider work inside the Worker-wide model concurrency limit."""

        stream = getattr(self._runtime, "invoke_stream", None)
        if stream is None:
            return await self.invoke(
                execution_spec,
                input_payload,
                snapshot,
                prompt_locale,
            )
        async with self._semaphore:
            return cast(
                UnvalidatedAgentOutput,
                await stream(
                    execution_spec,
                    input_payload,
                    snapshot,
                    prompt_locale,
                    sink,
                ),
            )


class SqlCheckpointPortAdapter:
    """Narrow the concrete checkpoint repository to the orchestrator Port."""

    def __init__(self, checkpoints: SqlCheckpointStore) -> None:
        self._checkpoints = checkpoints

    async def ensure(self, task_id: str, node_key: str, group: str) -> None:
        await self._checkpoints.ensure(task_id, node_key, group)

    async def get(self, task_id: str, node_key: str) -> CheckpointView:
        return await self._checkpoints.get(task_id, node_key)

    async def mark_running(self, task_id: str, node_key: str) -> None:
        await self._checkpoints.mark_running(task_id, node_key)

    async def mark_output_saved(
        self,
        task_id: str,
        node_key: str,
        reference: str,
        content_hash: str,
        review_completion_status: AgentReviewCompletionStatus,
    ) -> None:
        await self._checkpoints.mark_output_saved(
            task_id,
            node_key,
            reference,
            content_hash,
            review_completion_status,
        )

    async def mark_validating(self, task_id: str, node_key: str) -> None:
        await self._checkpoints.mark_validating(task_id, node_key)

    async def ensure_plan_nodes(
        self,
        plan: ReviewPlan,
        *,
        capability_fingerprints: dict[str, str] | None = None,
    ) -> None:
        await self._checkpoints.ensure_plan_nodes(
            plan, capability_fingerprints=capability_fingerprints
        )

    async def ensure_plan_node(
        self,
        node: ReviewPlanNode,
        *,
        capability_fingerprint: str | None = None,
    ) -> None:
        await self._checkpoints.ensure_plan_node(
            node,
            capability_fingerprint=capability_fingerprint,
        )

    async def list_for_task(self, task_id: str) -> tuple[CheckpointRecord, ...]:
        return await self._checkpoints.list_for_task(task_id)

    async def mark_failed(
        self,
        task_id: str,
        node_key: str,
        error_code: str,
        *,
        is_timeout: bool = False,
        failure_metadata: Mapping[str, str] | None = None,
    ) -> None:
        await self._checkpoints.mark_failed(
            task_id,
            node_key,
            error_code,
            is_timeout=is_timeout,
            failure_metadata=failure_metadata,
        )

    async def mark_skipped(self, task_id: str, node_key: str, reason_code: str) -> None:
        await self._checkpoints.mark_skipped(task_id, node_key, reason_code)

    async def cancel_non_terminal(self, task_id: str) -> None:
        await self._checkpoints.cancel_non_terminal(task_id)


class SqlJobQueuePortAdapter:
    """Narrow the concrete SQLite queue to the scheduler claim contract."""

    def __init__(self, queue: SqlJobQueue) -> None:
        self._queue = queue

    async def next_queued(self) -> ClaimedJob | None:
        return await self._queue.next_queued()


class WorkerReviewExecutor:
    """Reconstruct durable inputs and drive the restart-safe application orchestrator."""

    def __init__(
        self,
        *,
        settings: Settings,
        review_store: SqlReviewStore,
        worktree_registry: SqlWorktreeRegistry,
        worktree_lifecycle: ReviewWorktreeLifecycle,
        worktree_recovery: ReviewWorktreeRecoveryService,
        snapshot_service: SnapshotService,
        context_builder: ContextBuilder,
        excerpt_reader: SnapshotFileReaderPort,
        runtime: AgentRuntimePort,
        output_artifacts: FilesystemRunArtifactStore,
        checkpoints: SqlCheckpointStore,
        semaphores: WorkerSemaphores,
        transcripts: WorkerTranscriptStore,
        agent_prompts: AgentPromptSettingsService | None = None,
        repository_inspector: RepositoryInspector | None = None,
        provider_config: ModelProviderConfigPort | None = None,
        tool_limits_service: ToolLimitsService | None = None,
        capability_resolver: CapabilityResolver | None = None,
        execution_spec_store: SqlAgentExecutionSpecStore | None = None,
        review_plan_store: SqlReviewPlanStore | None = None,
        candidate_store: SqlCandidateFindingStore | None = None,
        verdict_store: SqlVerdictStore | None = None,
    ) -> None:
        self._settings = settings
        self._review_store = review_store
        self._worktree_registry = worktree_registry
        self._worktree_lifecycle = worktree_lifecycle
        self._worktree_recovery = worktree_recovery
        self._snapshot_service = snapshot_service
        self._context_builder = context_builder
        self._excerpt_reader = excerpt_reader
        self._runtime = _ModelLimitedRuntime(runtime, semaphores.model)
        self._output_artifacts = output_artifacts
        self._checkpoints = SqlCheckpointPortAdapter(checkpoints)
        self._semaphores = semaphores
        self._transcripts = transcripts
        self._agent_prompts = agent_prompts or AgentPromptSettingsService(
            FilesystemAgentPromptStore(settings.data_dir), settings.prompt_dir
        )
        self._repository_inspector = repository_inspector or RepositoryInspector(
            GitRepositoryMetadataAdapter(GitCli()),
            settings.repository_roots,
        )
        self._provider_config = provider_config or FilesystemModelProviderConfigAdapter(
            settings.data_dir
        )
        self._tool_limits_service = tool_limits_service or ToolLimitsService(
            FilesystemToolLimitsStore(
                settings.data_dir,
                load_web_settings_defaults(
                    settings.web_settings_defaults_config
                ).tool_limits,
            )
        )
        self._capability_resolver = capability_resolver or CapabilityResolver(
            builtin_capability_profiles(),
            builtin_skill_policies(),
        )
        self._execution_spec_store = execution_spec_store
        self._review_plan_store = review_plan_store
        self._candidate_store = candidate_store
        self._verdict_store = verdict_store
        self._verdict_codecs: dict[tuple[str, str], VerdictCodec] = {}
        self._dedup_codecs: dict[tuple[str, str], DedupCodec] = {}
        self._remediation_codecs: dict[tuple[str, str], RemediationCodec] = {}

    async def recover(self) -> None:
        """Recover Task 11 checkpoints and reconcile every registered owned worktree."""

        await self._review_store.recover_after_singleton_restart()
        active: dict[str, WorktreeRecoveryInput] = {}
        for record in await self._review_store.list_active_executions():
            await self._transcripts.append(
                record.task_id,
                "lifecycle",
                "Review execution recovered by Worker",
            )
            await self._validate_repository(record)
            active[record.task_id] = WorktreeRecoveryInput(
                repository=record.repository_path,
                captured=self._captured(record),
            )
        await self._worktree_recovery.reconcile(active)

    async def execute(self, task_id: str) -> None:
        """Execute one claimed task while sharing only bounded Worker semaphores."""

        orchestrator = ReviewOrchestrator(
            workflow=self._review_store,
            prepare=self.prepare,
            runtime=self._runtime,
            artifacts=self._output_artifacts,
            checkpoints=self._checkpoints,
            validator_factory=self._validator,
            completion=self._review_store,
            agent_semaphore=self._semaphores.agent,
            max_agent_runs_per_review=fair_per_review_agent_limit(
                configured_limit=self._settings.max_agent_runs_per_review,
                global_limit=self._settings.max_active_agent_runs,
                max_active_reviews=self._settings.max_active_reviews,
            ),
            prepare_verdict=self._prepare_verdict,
            prepare_dedup=self._prepare_dedup,
            prepare_remediation=self._prepare_remediation,
            publish_findings=self._publish_findings,
            transcript=self._transcripts,
        )
        await orchestrator.execute(task_id)
        await self._finalize_if_terminal(task_id)
        await self._cleanup_terminal_worktree(task_id)

    async def prepare(self, task_id: str) -> PreparedReview:
        """Rebuild a verified Snapshot and bounded Agent inputs from durable execution data."""

        record = await self._review_store.get_execution(task_id)
        if record is None:
            raise KeyError(task_id)
        existing_findings = ExistingFindingSet.from_json(
            record.existing_findings_json, record.existing_findings_hash
        )
        await self._validate_repository(record)
        captured = self._captured(record)
        worktree = await self._worktree_registry.get(task_id)
        if worktree is None:
            worktree = await self._worktree_lifecycle.create(
                task_id,
                record.repository_path,
                captured,
            )
        else:
            await self._worktree_lifecycle.verify_ownership(worktree)
        scope_plan = ScopePlan(
            base_oid=record.base_oid,
            head_oid=record.head_oid,
            candidate_paths=record.candidate_paths,
            capture_workspace_overlay=record.overlay_artifact_ref is not None,
            scope_type=record.scope_type,
            file_exclusion_policy=ReviewFileExclusionPolicy.from_json(
                record.file_exclusion_policy_json
            ),
        )
        instructions = await self._snapshot_service.resolve_instructions(
            worktree,
            record.candidate_paths,
        )
        snapshot = await self._snapshot_service.freeze(
            worktree,
            captured,
            scope_plan,
            instructions,
        )
        if not snapshot.manifest.review_paths:
            await self._review_store.record_empty_review_scope(task_id)
            await self._transcripts.append(
                task_id,
                "lifecycle",
                "Review scope is empty after frozen file exclusions; model execution skipped",
                metadata={"reason_code": "review_scope_empty"},
            )
            return PreparedReview(
                snapshot=snapshot,
                execution_specs=(),
                input_payloads={},
                prompt_locale=record.prompt_locale,
                existing_findings=existing_findings,
            )
        provider_config = await self._provider_config.load()
        if provider_config is None:
            provider_config = ModelProviderConfig(api_key="", model="", base_url="")
        tool_limits = await self._tool_limits_service.get()
        plan_record = (
            await self._review_plan_store.get(task_id)
            if self._review_plan_store is not None
            else None
        )
        stored_specs = (
            await load_frozen_execution_specs(
                task_id, self._execution_spec_store, self._output_artifacts
            )
            if self._execution_spec_store is not None
            else {}
        )
        selected_stored_specs = {
            spec.agent.reference: spec
            for spec in stored_specs.values()
            if spec.agent.reference in record.selected_agent_versions
        }
        if (
            plan_record is None
            and self._review_plan_store is not None
            and self._execution_spec_store is not None
            and isinstance(record.review_profile.reviewer_selection, FixedReviewerSelection)
        ):
            plan, execution_specs_by_node = await self._persist_fixed_plan(
                record,
                snapshot,
                provider_config,
                tool_limits.max_read_bytes,
                stored_specs,
            )
            base_input = self._context_builder.build(snapshot, instructions).canonical_bytes()
            return PreparedReview(
                snapshot=snapshot,
                execution_specs=tuple(execution_specs_by_node.values()),
                input_payloads=self._plan_payloads(
                    task_id, plan, execution_specs_by_node, base_input, existing_findings
                ),
                prompt_locale=record.prompt_locale,
                plan=plan,
                execution_specs_by_node=execution_specs_by_node,
                existing_findings=existing_findings,
            )
        if (
            plan_record is None
            and self._review_plan_store is not None
            and self._execution_spec_store is not None
            and isinstance(record.review_profile.reviewer_selection, AdaptiveReviewerSelection)
        ):
            return await self._persist_adaptive_plan(
                record,
                snapshot,
                instructions,
                provider_config,
                tool_limits.max_read_bytes,
                stored_specs,
            )
        if plan_record is not None:
            plan = plan_record.plan
            if set(stored_specs) != {node.node_id for node in plan.nodes}:
                raise ValueError("task has an incomplete frozen Plan execution spec set")
            execution_specs_by_node = {
                node.node_id: stored_specs[node.node_id] for node in plan.nodes
            }
            base_input = self._context_builder.build(snapshot, instructions).canonical_bytes()
            return PreparedReview(
                snapshot=snapshot,
                execution_specs=tuple(execution_specs_by_node.values()),
                input_payloads=self._plan_payloads(
                    task_id, plan, execution_specs_by_node, base_input, existing_findings
                ),
                prompt_locale=record.prompt_locale,
                plan=plan,
                execution_specs_by_node=execution_specs_by_node,
                existing_findings=existing_findings,
            )
        if stored_specs:
            if set(selected_stored_specs) != set(record.selected_agent_versions):
                raise ValueError("task has an incomplete frozen execution spec set")
            execution_specs = tuple(
                selected_stored_specs[reference] for reference in record.selected_agent_versions
            )
        else:
            execution_specs = await self._execution_specs(
                record.selected_agent_versions,
                record.prompt_locale,
                snapshot,
                AgentExecutionLimits(
                    max_turns=provider_config.max_agent_turns,
                    max_tool_calls=provider_config.max_tool_calls,
                    max_input_tokens=provider_config.max_tokens,
                    max_output_tokens=provider_config.max_tokens,
                    timeout_seconds=provider_config.agent_timeout,
                    max_tool_result_bytes=tool_limits.max_read_bytes,
                ),
            )
        payloads: dict[str, bytes] = {}
        for execution_spec in execution_specs:
            agent_input = self._context_builder.build(snapshot, instructions)
            # Fallback 路径无 plan，dedup 不会运行，不注入 existing_findings
            payloads[execution_spec.agent.reference] = agent_input.canonical_bytes()
        return PreparedReview(
            snapshot=snapshot,
            execution_specs=execution_specs,
            input_payloads=payloads,
            prompt_locale=record.prompt_locale,
            existing_findings=existing_findings,
        )

    async def _persist_adaptive_plan(
        self,
        record: ReviewExecutionRecord,
        snapshot: ReviewSnapshot,
        instructions: ResolvedInstructionSet,
        provider_config: ModelProviderConfig,
        max_tool_result_bytes: int,
        stored_specs: dict[str, FrozenAgentExecutionSpec],
    ) -> PreparedReview:
        """Run the Planner durably, then freeze its exact reviewer DAG before fan-out."""

        execution_spec_store = self._execution_spec_store
        review_plan_store = self._review_plan_store
        if execution_spec_store is None or review_plan_store is None:
            raise ValueError("Adaptive Review Plan persistence is unavailable")
        catalog = builtin_agent_catalog()
        eligible = tuple(
            reference
            for reference, agent in catalog.items()
            if agent.is_public
            and agent.planner_eligible
            and reference not in MANDATORY_ADAPTIVE_REVIEWERS
        )
        required_references = (
            *eligible,
            *MANDATORY_ADAPTIVE_REVIEWERS,
            "review-planner:v2",
            "review-verifier:v2",
            "review-deduplicator:v2",
            "review-remediator:v2",
        )
        specs_by_reference = {spec.agent.reference: spec for spec in stored_specs.values()}
        missing = tuple(
            reference for reference in required_references if reference not in specs_by_reference
        )
        if missing:
            generated = await self._execution_specs(
                missing,
                record.prompt_locale,
                snapshot,
                AgentExecutionLimits(
                    max_turns=provider_config.max_agent_turns,
                    max_tool_calls=provider_config.max_tool_calls,
                    max_input_tokens=provider_config.max_tokens,
                    max_output_tokens=provider_config.max_tokens,
                    timeout_seconds=provider_config.agent_timeout,
                    max_tool_result_bytes=max_tool_result_bytes,
                ),
            )
            specs_by_reference.update((spec.agent.reference, spec) for spec in generated)
        readiness = {
            reference: CapabilityReadiness("ready", ())
            for reference in (*eligible, *MANDATORY_ADAPTIVE_REVIEWERS)
        }
        base_input = self._context_builder.build(snapshot, instructions).canonical_bytes()
        risk_summary = ChangeRiskSummary.from_snapshot(snapshot)
        planner_input = build_planner_input_payload(
            base_input,
            eligible_reviewer_references=eligible,
            readiness=readiness,
            risk_summary=risk_summary,
            reviewer_catalog=tuple(
                {
                    "reference": reference,
                    "dimensions": list(catalog[reference].dimensions),
                }
                for reference in eligible
            ),
        )
        compiler = ReviewPlanCompiler(catalog)
        draft = compiler.compile(
            task_id=record.task_id,
            selection_mode="adaptive",
            reviewer_references=("general:v2",),
            planner_selection=PlannerSelection("2", ("general:v2",)),
            execution_specs=specs_by_reference,
            readiness=readiness,
        )
        planner_node = next(
            node for node in draft.nodes if node.node_type is ReviewPlanNodeType.PLANNER
        )
        planner_spec = specs_by_reference["review-planner:v2"]
        await self._persist_execution_spec(
            record.task_id, planner_node.node_id, planner_spec, stored_specs
        )
        await self._checkpoints.ensure_plan_node(
            planner_node,
            capability_fingerprint=planner_spec.fingerprint,
        )
        checkpoint = await self._checkpoints.get(record.task_id, planner_node.node_id)
        if checkpoint.status == "pending":
            if checkpoint.run_id is None:
                raise RuntimeError("Adaptive Planner checkpoint has no stable run identity")
            await self._checkpoints.mark_running(record.task_id, planner_node.node_id)
            output = await self._invoke_planner_observably(
                task_id=record.task_id,
                node_id=planner_node.node_id,
                run_id=checkpoint.run_id,
                execution_spec=planner_spec,
                input_payload=add_host_run_identity(planner_input, checkpoint.run_id),
                snapshot=snapshot,
                prompt_locale=record.prompt_locale,
            )
            artifact = await self._output_artifacts.write_output(
                planner_node.node_id, output.canonical_bytes
            )
            await self._checkpoints.mark_output_saved(
                record.task_id,
                planner_node.node_id,
                artifact.reference,
                artifact.content_hash,
                output.review_completion_status,
            )
            checkpoint = await self._checkpoints.get(record.task_id, planner_node.node_id)
        if checkpoint.status == "output_saved":
            await self._checkpoints.mark_validating(record.task_id, planner_node.node_id)
            checkpoint = await self._checkpoints.get(record.task_id, planner_node.node_id)
        if checkpoint.status not in {"validating", "succeeded"}:
            raise RuntimeError("Adaptive Planner checkpoint cannot be resumed")
        if checkpoint.artifact_ref is None or checkpoint.artifact_hash is None:
            raise RuntimeError("Adaptive Planner checkpoint has no durable output")
        planner_bytes = await self._output_artifacts.read_output(
            checkpoint.artifact_ref, checkpoint.artifact_hash
        )
        try:
            planner_payload = json.loads(planner_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Adaptive Planner output is not canonical JSON") from error
        if not isinstance(planner_payload, dict) or "reviewer_references" not in planner_payload:
            raise ValueError("Adaptive Planner output is missing reviewer_references")
        selection = PlannerOutputCodec(
            eligible_reviewer_references=eligible,
            unavailable_reviewer_references=(),
        ).decode_references(planner_payload["reviewer_references"])
        if checkpoint.status == "validating":
            await self._review_store.complete_planner_run(
                record.task_id, planner_node.node_id, selection.reviewer_references
            )
        compiled_plan = compiler.compile(
            task_id=record.task_id,
            selection_mode="adaptive",
            reviewer_references=selection.reviewer_references,
            planner_selection=selection,
            execution_specs=specs_by_reference,
            readiness=readiness,
        )
        specs_by_node = {
            node.node_id: specs_by_reference[node.agent_reference]
            for node in compiled_plan.nodes
        }
        for node_id, execution_spec in specs_by_node.items():
            if node_id == planner_node.node_id:
                continue
            await self._persist_execution_spec(
                record.task_id, node_id, execution_spec, stored_specs
            )
        plan = await ReviewPlanningService(
            compiler=compiler,
            planner=_SelectedAdaptivePlanning(selection),
            plan_store=review_plan_store,
        ).plan(
            task_id=record.task_id,
            profile=record.review_profile,
            execution_specs=specs_by_reference,
            readiness=readiness,
            candidate_paths=record.candidate_paths,
            catalog_version="builtin:v2",
            capability_fingerprint=hashlib.sha256(
                "".join(sorted(spec.fingerprint for spec in specs_by_reference.values())).encode()
            ).hexdigest(),
            risk_summary=risk_summary,
        )
        if plan != compiled_plan:
            raise ValueError("persisted Adaptive Review Plan changed during preparation")
        adaptive_existing_findings = ExistingFindingSet.from_json(
            record.existing_findings_json, record.existing_findings_hash
        )
        return PreparedReview(
            snapshot=snapshot,
            execution_specs=tuple(specs_by_node.values()),
            input_payloads=self._plan_payloads(
                record.task_id,
                plan,
                specs_by_node,
                base_input,
                adaptive_existing_findings,
            ),
            prompt_locale=record.prompt_locale,
            plan=plan,
            execution_specs_by_node=specs_by_node,
            existing_findings=adaptive_existing_findings,
        )

    async def _invoke_planner_observably(
        self,
        *,
        task_id: str,
        node_id: str,
        run_id: str,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        """Stream one Planner invocation into the task transcript with durable run identity."""

        identity = {
            "agent": execution_spec.agent.reference,
            "node_id": node_id,
            "run_id": run_id,
        }

        async def record_event(event: AgentRuntimeEvent) -> None:
            await self._transcripts.append(
                task_id,
                event.kind,
                event.content,
                metadata={**event.metadata, **identity},
            )

        output = await self._runtime.invoke_stream(
            execution_spec,
            input_payload,
            snapshot,
            prompt_locale,
            record_event,
        )
        await self._transcripts.append(
            task_id,
            "model_output",
            output.canonical_bytes.decode("utf-8", errors="replace"),
            metadata={
                **identity,
                "usage_scope": "agent_run",
                "model_name": output.model_name,
                "llm_call_count": str(len(output.diagnostics)),
                "input_tokens": str(output.input_tokens),
                "cached_input_tokens": str(output.cached_input_tokens),
                "context_compaction_count": str(output.context_compaction_count),
                "context_compacted_result_count": str(
                    output.context_compacted_result_count
                ),
                "context_compaction_original_tokens": str(
                    output.context_compaction_original_tokens
                ),
                "context_compaction_compressed_tokens": str(
                    output.context_compaction_compressed_tokens
                ),
                "output_tokens": str(output.output_tokens),
                "total_tokens": str(output.input_tokens + output.output_tokens),
            },
        )
        return output

    async def _persist_execution_spec(
        self,
        task_id: str,
        node_id: str,
        execution_spec: FrozenAgentExecutionSpec,
        stored_specs: Mapping[str, FrozenAgentExecutionSpec],
    ) -> None:
        """Persist one immutable node specification without rewriting recovered artifacts."""

        if node_id in stored_specs:
            if stored_specs[node_id] != execution_spec:
                raise ValueError("frozen Plan execution spec conflicts with stored data")
            return
        execution_spec_store = self._execution_spec_store
        if execution_spec_store is None:
            raise ValueError("Review Plan execution spec persistence is unavailable")
        prompt_artifact = await self._output_artifacts.write_output(
            f"spec-prompt:{task_id}:{node_id}", execution_spec.agent.prompt_template.encode()
        )
        skill_artifacts: list[ArtifactIdentity] = []
        for ordinal, skill in enumerate(execution_spec.skills):
            artifact = await self._output_artifacts.write_output(
                f"spec-skill:{task_id}:{node_id}:{ordinal}", skill.instruction_text.encode()
            )
            skill_artifacts.append(ArtifactIdentity(artifact.reference, artifact.content_hash))
        await execution_spec_store.save(
            task_id=task_id,
            logical_node_id=node_id,
            execution_spec=execution_spec,
            prompt_artifact_ref=prompt_artifact.reference,
            prompt_artifact_hash=prompt_artifact.content_hash,
            skill_artifacts=tuple(skill_artifacts),
        )

    async def _persist_fixed_plan(
        self,
        record: ReviewExecutionRecord,
        snapshot: ReviewSnapshot,
        provider_config: ModelProviderConfig,
        max_tool_result_bytes: int,
        stored_specs: dict[str, FrozenAgentExecutionSpec],
    ) -> tuple[ReviewPlan, dict[str, FrozenAgentExecutionSpec]]:
        """Compile a host-owned Fixed DAG and freeze every node before fan-out."""

        selection = record.review_profile.reviewer_selection
        if not isinstance(selection, FixedReviewerSelection):
            raise ValueError("Fixed Review Plan preparation requires a fixed selection")
        execution_spec_store = self._execution_spec_store
        review_plan_store = self._review_plan_store
        if execution_spec_store is None or review_plan_store is None:
            raise ValueError("Fixed Review Plan persistence is unavailable")
        required_references = list(selection.reviewer_versions)
        if len(selection.reviewer_versions) > 1:
            required_references.append("review-verifier:v2")
        required_references.append("review-deduplicator:v2")
        required_references.append("review-remediator:v2")
        specs_by_reference = {spec.agent.reference: spec for spec in stored_specs.values()}
        missing_references = tuple(
            reference for reference in required_references if reference not in specs_by_reference
        )
        if missing_references:
            generated = await self._execution_specs(
                missing_references,
                record.prompt_locale,
                snapshot,
                AgentExecutionLimits(
                    max_turns=provider_config.max_agent_turns,
                    max_tool_calls=provider_config.max_tool_calls,
                    max_input_tokens=provider_config.max_tokens,
                    max_output_tokens=provider_config.max_tokens,
                    timeout_seconds=provider_config.agent_timeout,
                    max_tool_result_bytes=max_tool_result_bytes,
                ),
            )
            specs_by_reference.update((spec.agent.reference, spec) for spec in generated)
        catalog = builtin_agent_catalog()
        plan = ReviewPlanCompiler(catalog).compile(
            task_id=record.task_id,
            selection_mode="fixed",
            reviewer_references=selection.reviewer_versions,
            planner_selection=None,
            execution_specs=specs_by_reference,
            readiness={
                reference: CapabilityReadiness("ready", ())
                for reference in selection.reviewer_versions
            },
        )
        specs_by_node = {
            node.node_id: specs_by_reference[node.agent_reference] for node in plan.nodes
        }
        for node_id, execution_spec in specs_by_node.items():
            if node_id in stored_specs:
                if stored_specs[node_id] != execution_spec:
                    raise ValueError("frozen Plan execution spec conflicts with stored data")
                continue
            prompt_artifact = await self._output_artifacts.write_output(
                f"spec-prompt:{record.task_id}:{node_id}",
                execution_spec.agent.prompt_template.encode("utf-8"),
            )
            skill_artifacts: list[ArtifactIdentity] = []
            for ordinal, skill in enumerate(execution_spec.skills):
                artifact = await self._output_artifacts.write_output(
                    f"spec-skill:{record.task_id}:{node_id}:{ordinal}",
                    skill.instruction_text.encode("utf-8"),
                )
                skill_artifacts.append(ArtifactIdentity(artifact.reference, artifact.content_hash))
            await execution_spec_store.save(
                task_id=record.task_id,
                logical_node_id=node_id,
                execution_spec=execution_spec,
                prompt_artifact_ref=prompt_artifact.reference,
                prompt_artifact_hash=prompt_artifact.content_hash,
                skill_artifacts=tuple(skill_artifacts),
            )
        catalog_payload = json.dumps(
            {
                reference: catalog[reference].content_hash
                for reference in sorted(required_references)
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        capability_payload = json.dumps(
            {
                node_id: execution_spec.fingerprint
                for node_id, execution_spec in sorted(specs_by_node.items())
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        persisted_plan = await ReviewPlanningService(
            compiler=ReviewPlanCompiler(catalog),
            planner=_RejectAdaptivePlanning(),
            plan_store=review_plan_store,
        ).plan(
            task_id=record.task_id,
            profile=record.review_profile,
            execution_specs=specs_by_reference,
            readiness={
                reference: CapabilityReadiness("ready", ())
                for reference in selection.reviewer_versions
            },
            candidate_paths=record.candidate_paths,
            catalog_version=f"builtin:{hashlib.sha256(catalog_payload.encode()).hexdigest()}",
            capability_fingerprint=hashlib.sha256(capability_payload.encode()).hexdigest(),
        )
        if persisted_plan != plan:
            raise ValueError("persisted Fixed Review Plan changed during preparation")
        return persisted_plan, specs_by_node

    @staticmethod
    def _plan_payloads(
        task_id: str,
        plan: ReviewPlan,
        execution_specs_by_node: dict[str, FrozenAgentExecutionSpec],
        base_input: bytes,
        existing_findings: ExistingFindingSet,
    ) -> dict[str, bytes]:
        """Attach host-only stable identities to every immutable Plan node input."""

        guidance_by_reference = {
            guidance.reviewer_reference: guidance for guidance in plan.reviewer_guidance
        }
        payloads: dict[str, bytes] = {}
        for node in plan.nodes:
            guidance = guidance_by_reference.get(node.agent_reference)
            visible_payload = (
                add_reviewer_plan_guidance(
                    base_input,
                    reason_codes=guidance.reason_codes,
                    focus_paths=guidance.focus_paths,
                )
                if guidance is not None
                else base_input
            )
            if node.node_type is ReviewPlanNodeType.DEDUPLICATOR:
                visible_payload = add_existing_findings_context(visible_payload, existing_findings)
            elif node.node_type is ReviewPlanNodeType.REMEDIATOR:
                visible_payload = add_existing_findings_context(visible_payload, existing_findings)
            run = AgentRun.create(
                task_id=task_id,
                agent_version=node.agent_reference,
                pass_index=node.pass_index,
                shard_id=node.shard_id,
                logical_attempt_group=node.logical_attempt_group,
                node_role=node.node_type.value,
                capability_fingerprint=execution_specs_by_node[node.node_id].fingerprint,
            )
            payloads[node.node_id] = add_host_run_identity(visible_payload, run.run_id)
        return payloads

    async def record_failure(self, task_id: str, error: Exception) -> None:
        """Record a stable, readable failure without provider response content."""

        diagnostic = _failure_diagnostic(error)

        await self._transcripts.append(
            task_id,
            "lifecycle",
            diagnostic.content,
            metadata=diagnostic.metadata,
        )
        try:
            await self._review_store.fail(task_id, "review_execution_failed")
        except InvalidAgentRunStateError:
            pass
        await self._transcripts.finalize(task_id)
        await self._cleanup_terminal_worktree(task_id)

    async def _finalize_if_terminal(self, task_id: str) -> None:
        record = await self._review_store.get_review(task_id)
        if record is not None and record.status in _TERMINAL_STATUSES:
            await self._transcripts.finalize(task_id)

    async def _cleanup_terminal_worktree(self, task_id: str) -> None:
        """Remove a verified checkout only after its durable task becomes terminal."""

        record = await self._review_store.get_review(task_id)
        if record is None or record.status not in _TERMINAL_STATUSES:
            return
        worktree = await self._worktree_registry.get(task_id)
        if worktree is not None:
            try:
                await self._worktree_lifecycle.remove_owned(worktree)
            except Exception:
                # Cleanup failures should not crash the service
                pass

    async def _validate_repository(self, record: ReviewExecutionRecord) -> None:
        repository = await self._repository_inspector.inspect(record.repository_path)
        if (
            repository.repository_realpath_hash != record.repository_realpath_hash
            or repository.git_common_dir_hash != record.git_common_dir_hash
        ):
            raise ValueError("durable repository identity no longer matches")

    @staticmethod
    def _captured(record: ReviewExecutionRecord) -> CapturedReviewInput:
        if (record.overlay_hash is None) != (record.overlay_artifact_ref is None):
            raise ValueError("durable overlay identity is incomplete")
        artifact = (
            OpaqueArtifact(record.overlay_artifact_ref, record.overlay_hash, 0)
            if record.overlay_artifact_ref is not None and record.overlay_hash is not None
            else None
        )
        return CapturedReviewInput(
            target=ReviewTarget(record.base_oid, record.head_oid, record.overlay_hash),
            overlay_artifact=artifact,
        )

    async def _execution_specs(
        self,
        references: tuple[str, ...],
        locale: str,
        snapshot: ReviewSnapshot,
        execution_limits: AgentExecutionLimits,
    ) -> tuple[FrozenAgentExecutionSpec, ...]:
        catalog = builtin_agent_catalog()
        try:
            specs: list[FrozenAgentExecutionSpec] = []
            for reference in references:
                agent = catalog[reference]
                view = await self._agent_prompts.get(
                    agent, "zh-CN" if locale == "zh-CN" else "en"
                )
                resolved_agent = replace(agent, prompt_template=view.prompt)
                specs.append(
                    self._capability_resolver.resolve(
                        agent=resolved_agent,
                        prompt_content_hash=hashlib.sha256(view.prompt.encode("utf-8")).hexdigest(),
                        facts=self._skill_facts(snapshot),
                        execution_limits=execution_limits,
                    )
                )
            return tuple(specs)
        except KeyError as error:
            raise ValueError("review references an unavailable Agent version") from error

    @staticmethod
    def _skill_facts(snapshot: ReviewSnapshot) -> SkillActivationFacts:
        language_by_suffix = {
            ".cs": "csharp",
            ".go": "go",
            ".java": "java",
            ".js": "javascript",
            ".jsx": "javascript",
            ".py": "python",
            ".rb": "ruby",
            ".rs": "rust",
            ".ts": "typescript",
            ".tsx": "typescript",
        }
        changed_paths = tuple(sorted(snapshot.manifest.review_paths))
        languages = tuple(
            sorted(
                {
                    language
                    for path in changed_paths
                    for suffix, language in language_by_suffix.items()
                    if path.casefold().endswith(suffix)
                }
            )
        )
        return SkillActivationFacts(languages, changed_paths)

    def _validator(
        self,
        task_id: str,
        node_key: str,
        prepared: PreparedReview,
        agent: AgentVersion,
        checkpoint: CheckpointRecord,
    ) -> CandidateValidator | VerdictValidator | DedupValidator | RemediationValidator:
        if agent.role.value == "deduplicator":
            dedup_codec = self._dedup_codecs.get((task_id, node_key))
            if dedup_codec is None:
                raise ValueError("Dedup constraints were not prepared")
            return DedupValidator(dedup_codec)
        if agent.role.value == "remediator":
            remediation_codec = self._remediation_codecs.get((task_id, node_key))
            if remediation_codec is None:
                raise ValueError("Remediation constraints were not prepared")
            return RemediationValidator(remediation_codec)
        if agent.role.value == "verifier":
            verdict_codec = self._verdict_codecs.get((task_id, node_key))
            if verdict_codec is None:
                raise ValueError("Verdict constraints were not prepared")
            return VerdictValidator(verdict_codec)
        if checkpoint.run_id is None:
            raise ValueError("Candidate AgentRun lacks a stable run ID")
        return CandidateValidator(
            task_id=task_id,
            run_id=checkpoint.run_id,
            snapshot=prepared.snapshot,
            agent=agent,
            excerpt_reader=self._excerpt_reader,
        )

    async def _prepare_verdict(self, task_id: str, prepared: PreparedReview) -> bool:
        """Persist deterministic clusters and prepare Final Verifier input.

        Returns ``True`` when the verifier should be skipped (zero clusters).
        """

        if self._candidate_store is None or self._verdict_store is None:
            return False
        plan = prepared.plan
        if plan is None:
            return False
        candidates = await self._candidate_store.list_for_task(task_id)
        service = ClusterService(self._verdict_store)
        clusters = await service.prepare(
            task_id=task_id,
            snapshot_id=prepared.snapshot.snapshot_id,
            candidates=candidates,
        )
        verifier = next(
            (node for node in plan.nodes if node.node_type.value == "verifier"),
            None,
        )
        if verifier is None:
            await self._verdict_store.save_decisions(task_id, publish_all_verdicts(clusters))
            return True
        if not clusters:
            await self._verdict_store.save_decisions(task_id, publish_all_verdicts(clusters))
            return True
        verifier_envelope = json.loads(prepared.input_payloads[verifier.node_id])
        verifier_role_context = verifier_envelope.setdefault("role_context", {})
        if not isinstance(verifier_role_context, dict):
            raise ValueError("Verifier role context must be an object")
        candidate_context_by_id: dict[str, dict[str, object]] = {}
        verifier_spec = prepared.execution_specs_by_node[verifier.node_id]
        for candidate in candidates:
            location = candidate.primary_location
            excerpt = await self._excerpt_reader.read(
                prepared.snapshot,
                location.path,
                location.start_line,
                location.end_line,
                location.side,
                verifier_spec.execution_limits.max_tool_result_bytes,
            )
            candidate_context_by_id[candidate.candidate_id] = {
                "candidate_id": candidate.candidate_id,
                "reviewer_reference": candidate.reviewer_reference,
                "location": {
                    "path": location.path,
                    "side": location.side,
                    "start_line": location.start_line,
                    "end_line": location.end_line,
                    "excerpt_hash": location.excerpt_hash,
                },
                "existing_code": excerpt.content.decode("utf-8", errors="replace"),
                "existing_code_hash": candidate.existing_code_hash,
                "is_existing_code_truncated": excerpt.truncated,
                "title": candidate.title,
                "content": candidate.content,
                "recommendation": candidate.recommendation,
                "category": candidate.category,
                "severity": candidate.severity.value,
                "primary_dimension": candidate.primary_dimension,
                "evidence_strength": candidate.evidence_strength.value,
            }
        verifier_role_context["verdict_context"] = {
            "clusters": [
                {
                    "cluster_id": cluster.cluster_id,
                    "canonical_candidate_id": cluster.canonical_candidate_id,
                    "candidate_ids": list(cluster.candidate_ids),
                    "title": cluster.title,
                    "category": cluster.category,
                    "severity": cluster.severity.value,
                    "content": cluster.content,
                    "recommendation": cluster.recommendation,
                    "primary_dimension": cluster.primary_dimension,
                    "evidence_strength": cluster.evidence_strength.value,
                    "candidates": [
                        candidate_context_by_id[candidate_id]
                        for candidate_id in cluster.candidate_ids
                        if candidate_id in candidate_context_by_id
                    ],
                }
                for cluster in clusters
            ],
            "schema_version": "2",
        }
        prepared.input_payloads[verifier.node_id] = json.dumps(
            verifier_envelope, sort_keys=True, separators=(",", ":")
        ).encode()
        self._verdict_codecs[(task_id, verifier.node_id)] = VerdictCodec(clusters=clusters)
        return False

    async def _publish_findings(self, task_id: str) -> None:
        if self._candidate_store is None or self._verdict_store is None:
            return
        candidates = await self._candidate_store.list_for_task(task_id)
        clusters = await self._verdict_store.list_clusters(task_id)
        verdicts = await self._verdict_store.list_decisions(task_id)
        denied_ids = await self._review_store.list_denied_verdict_ids(task_id)
        if denied_ids:
            verdicts = tuple(
                verdict
                for verdict in verdicts
                if verdict_decision_id(task_id, verdict.cluster_ids) not in denied_ids
            )
        publications = tuple(
            (verdict.cluster_ids[0], finding)
            for verdict in verdicts
            for finding in FindingPublisher.build(
                task_id=task_id,
                candidates=candidates,
                verdicts=(verdict,),
                clusters=clusters,
            )
        )
        await self._review_store.publish_verdict_findings(task_id, verdicts, publications)

    async def _prepare_dedup(self, task_id: str, prepared: PreparedReview) -> bool:
        """Prepare Deduplicator input and deterministic pre-filter.

        Returns ``True`` when the deduplicator should be skipped.
        """

        if self._candidate_store is None or self._verdict_store is None:
            return True
        existing = prepared.existing_findings
        if existing is None or not existing.items:
            return True
        plan = prepared.plan
        if plan is None:
            return True
        dedup_node = next(
            (
                node
                for node in plan.nodes
                if node.node_type is ReviewPlanNodeType.DEDUPLICATOR
            ),
            None,
        )
        if dedup_node is None:
            return True
        verdicts = await self._verdict_store.list_decisions(task_id)
        clusters = await self._verdict_store.list_clusters(task_id)
        candidates = await self._candidate_store.list_for_task(task_id)
        dedup_spec = prepared.execution_specs_by_node.get(dedup_node.node_id)
        max_read_bytes = (
            dedup_spec.execution_limits.max_tool_result_bytes
            if dedup_spec is not None
            else 65536
        )
        survived = await self._build_survived_findings(
            task_id, verdicts, clusters, candidates, prepared, max_read_bytes
        )
        if not survived:
            return True
        deterministic_denies = run_deterministic_filter(survived, existing.items)
        if deterministic_denies:
            await self._review_store.save_dedup_decisions(
                task_id, tuple(deterministic_denies)
            )
        denied_set = {
            decision.verdict_decision_id for decision in deterministic_denies
        }
        unresolved = tuple(
            finding for finding in survived if finding.verdict_decision_id not in denied_set
        )
        if not unresolved:
            return True
        dedup_context = {
            "schema_version": "1",
            "survived_findings": [finding.as_payload() for finding in unresolved],
        }
        envelope = json.loads(prepared.input_payloads[dedup_node.node_id])
        role_context = envelope.setdefault("role_context", {})
        if not isinstance(role_context, dict):
            raise ValueError("Deduplicator role context must be an object")
        role_context["dedup_context"] = dedup_context
        prepared.input_payloads[dedup_node.node_id] = json.dumps(
            envelope, sort_keys=True, separators=(",", ":")
        ).encode()
        self._dedup_codecs[(task_id, dedup_node.node_id)] = DedupCodec(
            expected_ids=frozenset(
                finding.verdict_decision_id for finding in unresolved
            )
        )
        return False

    async def _prepare_remediation(self, task_id: str, prepared: PreparedReview) -> bool:
        """Prepare Remediator input and deterministic pre-filter.

        Returns ``True`` when the remediator should be skipped.
        """

        existing = prepared.existing_findings
        if existing is None or not existing.items:
            return True
        plan = prepared.plan
        if plan is None:
            return True
        remediator_node = next(
            (
                node
                for node in plan.nodes
                if node.node_type is ReviewPlanNodeType.REMEDIATOR
            ),
            None,
        )
        if remediator_node is None:
            return True
        record = await self._review_store.get_execution(task_id)
        if record is not None and record.scope_type == "full":
            return True
        pending = tuple(
            PendingRemediation(
                remediation_ref=f"{item.source_id}:{item.finding_id}",
                source_id=item.source_id,
                finding_id=item.finding_id,
                title=item.title,
                content=item.content,
                path=item.path,
                side=item.side,
                start_line=item.start_line,
                end_line=item.end_line,
                existing_code=item.existing_code,
                category=item.category,
                severity=item.severity,
                recommendation=item.recommendation,
            )
            for item in existing.items
        )
        changed_paths = frozenset(prepared.snapshot.manifest.review_paths)
        deterministic = run_deterministic_remediation_filter(pending, changed_paths)
        if deterministic:
            await self._review_store.save_remediation_decisions(
                task_id, tuple(deterministic)
            )
        resolved_refs = {
            f"{d.source_id}:{d.finding_id}" for d in deterministic
        }
        unresolved = tuple(
            p for p in pending if p.remediation_ref not in resolved_refs
        )
        if not unresolved:
            return True
        remediation_context = {
            "schema_version": "1",
            "pending_findings": [p.as_payload() for p in unresolved],
        }
        envelope = json.loads(prepared.input_payloads[remediator_node.node_id])
        role_context = envelope.setdefault("role_context", {})
        if not isinstance(role_context, dict):
            raise ValueError("Remediator role context must be an object")
        role_context["remediation_context"] = remediation_context
        prepared.input_payloads[remediator_node.node_id] = json.dumps(
            envelope, sort_keys=True, separators=(",", ":")
        ).encode()
        self._remediation_codecs[(task_id, remediator_node.node_id)] = RemediationCodec(
            expected_refs=frozenset(
                p.remediation_ref for p in unresolved
            )
        )
        return False

    async def _build_survived_findings(
        self,
        task_id: str,
        verdicts: tuple[VerdictDecision, ...],
        clusters: tuple[FindingCluster, ...],
        candidates: tuple[CandidateFinding, ...],
        prepared: PreparedReview,
        max_read_bytes: int,
    ) -> tuple[SurvivedFinding, ...]:
        """Resolve publishable verdicts to flat survived findings for dedup."""

        by_candidate = {item.candidate_id: item for item in candidates}
        cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
        survived: list[SurvivedFinding] = []
        for verdict in verdicts:
            if not verdict.is_publishable:
                continue
            primary_cluster = cluster_by_id.get(verdict.cluster_ids[0])
            if primary_cluster is None:
                continue
            decision_id = verdict_decision_id(task_id, verdict.cluster_ids)
            if verdict.outcome is VerdictOutcome.MERGE:
                assert verdict.primary_location is not None
                assert verdict.existing_code is not None
                survived.append(
                    SurvivedFinding(
                        verdict_decision_id=decision_id,
                        cluster_ids=verdict.cluster_ids,
                        title=verdict.title or "",
                        content=verdict.content or "",
                        path=verdict.path,
                        side=verdict.side,
                        start_line=verdict.primary_location.start_line,
                        end_line=verdict.primary_location.end_line,
                        existing_code=verdict.existing_code,
                        category=verdict.category,
                        severity=verdict.severity.value if verdict.severity else None,
                        recommendation=verdict.recommendation,
                    )
                )
            else:
                canonical = by_candidate.get(primary_cluster.canonical_candidate_id)
                if canonical is None:
                    continue
                location = canonical.primary_location
                excerpt = await self._excerpt_reader.read(
                    prepared.snapshot,
                    location.path,
                    location.start_line,
                    location.end_line,
                    location.side,
                    max_read_bytes,
                )
                survived.append(
                    SurvivedFinding(
                        verdict_decision_id=decision_id,
                        cluster_ids=verdict.cluster_ids,
                        title=primary_cluster.title,
                        content=primary_cluster.content,
                        path=location.path,
                        side=location.side,
                        start_line=location.start_line,
                        end_line=location.end_line,
                        existing_code=excerpt.content.decode(
                            "utf-8", errors="replace"
                        ),
                        category=primary_cluster.category,
                        severity=primary_cluster.severity.value,
                        recommendation=primary_cluster.recommendation,
                    )
                )
        return tuple(survived)


def _failure_summary(error: AgentRuntimeError) -> str:
    """Keep a safe actionable summary in the durable transcript, never provider payload text."""

    summaries = {
        "provider_server_error": (
            "The model gateway is temporarily unavailable. Retry the review; if it persists, "
            "check the gateway service and Base URL."
        ),
        "provider_rate_limited": (
            "The model gateway rate limit was reached. Wait briefly, then retry the review."
        ),
        "provider_timeout": (
            "The model gateway did not respond before its request timeout. Check the gateway "
            "service, then retry the review."
        ),
        "agent_run_timeout": (
            "The Agent did not finish within the configured timeout. Narrow the review scope "
            "or increase the gateway timeout."
        ),
        "provider_connection_error": (
            "CodeLens could not connect to the model gateway. Check its Base URL and network "
            "availability, then retry."
        ),
        "provider_request_rejected": (
            "The model gateway rejected the request. Check the model, API type, and gateway "
            "settings."
        ),
        "max_model_turns_exceeded": (
            "The agent reached its maximum tool-use turns. Narrow the review scope or adjust "
            "the agent configuration."
        ),
        "invalid_model_output": (
            "The model returned a response that could not be used by the review workflow. "
            "Check model compatibility and retry."
        ),
        "missing_model_output": (
            "The model finished without a usable review result. Check model compatibility and "
            "retry."
        ),
        "invalid_comment_output": (
            "The model submitted review comments that could not be validated against the frozen "
            "Snapshot. Retry or narrow the review scope."
        ),
    }
    return summaries.get(
        error.reason_code,
        "The model invocation failed. Check the execution details and model gateway settings.",
    )


def _failure_diagnostic(error: Exception) -> _FailureDiagnostic:
    """Classify every Worker failure without copying exception messages into user artifacts."""

    metadata = {"error_type": type(error).__name__}
    if isinstance(error, AgentRuntimeError):
        metadata.update(error.failure_metadata())
        return _FailureDiagnostic(_failure_summary(error), metadata)

    if isinstance(error, DomainError):
        reason_code = {
            "invalid_repository": "repository_validation_failed",
            "snapshot_stale": "repository_changed_during_review",
            "worktree_ownership": "review_worktree_ownership_failed",
            "worktree_mutated": "review_worktree_mutated",
        }.get(error.code, "domain_execution_error")
        metadata.update(
            {
                "error_code": error.code,
                "reason_code": reason_code,
                "retryable": "false",
            }
        )
        content = {
            "repository_validation_failed": (
                "CodeLens could not create a trusted Snapshot from the selected repository. "
                "Verify the repository and selected revisions, then retry."
            ),
            "repository_changed_during_review": (
                "The repository changed while CodeLens was freezing its Snapshot. Retry from a "
                "stable repository state."
            ),
            "review_worktree_ownership_failed": (
                "CodeLens could not verify ownership of the isolated Review worktree. Remove "
                "stale task data or restart CodeLens, then retry."
            ),
            "review_worktree_mutated": (
                "The isolated Review worktree changed after it was frozen. Restart the Review "
                "from a clean task."
            ),
        }.get(
            reason_code,
            "A review domain rule prevented execution from completing. Check the task details.",
        )
        return _FailureDiagnostic(content, metadata)

    metadata.update(
        {
            "error_code": "unexpected_review_error",
            "reason_code": "internal_review_error",
            "retryable": "false",
        }
    )
    return _FailureDiagnostic(
        "CodeLens encountered an unexpected internal error. Use the task ID to inspect "
        "worker.log, then retry after correcting the reported cause.",
        metadata,
    )

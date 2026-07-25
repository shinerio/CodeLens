"""Worker-side reconstruction and execution of durable review commands."""

import asyncio
from dataclasses import dataclass, replace

from codelens.bootstrap.settings import Settings
from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.review.application.context_builder import ContextBuilder
from codelens.review.application.orchestrator import (
    CheckpointView,
    PreparedReview,
    ReviewOrchestrator,
)
from codelens.review.application.validate_findings import FindingValidator
from codelens.review.domain.agent_run import InvalidAgentRunStateError
from codelens.review.domain.errors import AgentRuntimeError
from codelens.review.domain.ports import (
    AgentRuntimeEventSink,
    AgentRuntimePort,
    ReviewExecutionRecord,
    SnapshotFileReaderPort,
    UnvalidatedAgentOutput,
)
from codelens.review.infrastructure.repositories import (
    SqlCheckpointStore,
    SqlJobQueue,
    SqlReviewStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.review.infrastructure.transcripts import WorkerTranscriptStore
from codelens.reviewer_catalog.application.prompt_settings import ReviewerPromptSettingsService
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.infrastructure.builtin_agents import correctness_agent
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemReviewerPromptStore,
)
from codelens.shared.domain.errors import DomainError
from codelens.worker.scheduler import ClaimedJob, WorkerSemaphores
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
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter

_TERMINAL_STATUSES = {"completed", "partial", "failed", "canceled"}


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
        agent: AgentVersion,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        async with self._semaphore:
            return await self._runtime.invoke(agent, input_payload, snapshot, prompt_locale)

    async def invoke_stream(
        self,
        agent: AgentVersion,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
        sink: AgentRuntimeEventSink,
    ) -> UnvalidatedAgentOutput:
        """Keep streamed provider work inside the Worker-wide model concurrency limit."""

        stream = getattr(self._runtime, "invoke_stream", None)
        if stream is None:
            return await self.invoke(agent, input_payload, snapshot, prompt_locale)
        async with self._semaphore:
            return await stream(agent, input_payload, snapshot, prompt_locale, sink)


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
    ) -> None:
        await self._checkpoints.mark_output_saved(
            task_id,
            node_key,
            reference,
            content_hash,
        )

    async def mark_validating(self, task_id: str, node_key: str) -> None:
        await self._checkpoints.mark_validating(task_id, node_key)


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
        codec: AgentOutputCodec,
        semaphores: WorkerSemaphores,
        transcripts: WorkerTranscriptStore,
        reviewer_prompts: ReviewerPromptSettingsService | None = None,
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
        self._codec = codec
        self._semaphores = semaphores
        self._transcripts = transcripts
        self._reviewer_prompts = reviewer_prompts or ReviewerPromptSettingsService(
            FilesystemReviewerPromptStore(settings.data_dir), settings.prompt_dir
        )
        self._repository_inspector = RepositoryInspector(
            GitRepositoryMetadataAdapter(GitCli()),
            settings.repository_roots,
        )

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

        await self._transcripts.append(task_id, "lifecycle", "Review execution started")
        orchestrator = ReviewOrchestrator(
            workflow=self._review_store,
            prepare=self.prepare,
            runtime=self._runtime,
            artifacts=self._output_artifacts,
            checkpoints=self._checkpoints,
            validator_factory=self._validator,
            completion=self._review_store,
            agent_semaphore=self._semaphores.agent,
            max_agent_runs_per_review=self._settings.max_agent_runs_per_review,
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
            target_paths=record.target_paths,
            capture_workspace_overlay=record.overlay_artifact_ref is not None,
            scope_type=record.scope_type,
        )
        instructions = await self._snapshot_service.resolve_instructions(
            worktree,
            record.target_paths,
        )
        snapshot = await self._snapshot_service.freeze(
            worktree,
            captured,
            scope_plan,
            instructions,
        )
        agents = await self._agents(record.selected_agent_versions, record.prompt_locale)
        payloads: dict[str, bytes] = {}
        for agent in agents:
            agent_input = self._context_builder.build(snapshot, instructions)
            payloads[f"{agent.agent_id}:v{agent.version}"] = agent_input.canonical_bytes()
        return PreparedReview(
            snapshot=snapshot,
            agents=agents,
            input_payloads=payloads,
            prompt_locale=record.prompt_locale,
        )

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

    async def _agents(self, references: tuple[str, ...], locale: str) -> tuple[AgentVersion, ...]:
        catalog = {"correctness:v1": correctness_agent()}
        try:
            agents: list[AgentVersion] = []
            for reference in references:
                agent = catalog[reference]
                view = await self._reviewer_prompts.get(
                    agent.agent_id, "zh-CN" if locale == "zh-CN" else "en"
                )
                agents.append(
                    replace(
                        agent,
                        prompt_template=view.prompt,
                    )
                )
            return tuple(agents)
        except KeyError as error:
            raise ValueError("review references an unavailable Agent version") from error

    def _validator(
        self,
        task_id: str,
        node_key: str,
        prepared: PreparedReview,
        agent: AgentVersion,
    ) -> FindingValidator:
        return FindingValidator(
            task_id=task_id,
            node_key=node_key,
            snapshot=prepared.snapshot,
            agent=agent,
            codec=self._codec,
            excerpt_reader=self._excerpt_reader,
        )


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

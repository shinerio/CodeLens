"""Worker composition root and process entry point."""

import asyncio
import signal
from dataclasses import dataclass

from codelens.bootstrap.logging import configure_process_logging
from codelens.bootstrap.settings import Settings
from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.instruction_policy.application.resolver import InstructionResolver
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser
from codelens.instruction_policy.infrastructure.structured_skip import StructuredSkipMatcher
from codelens.review.application.context_builder import ContextBuilder
from codelens.review.domain.ports import AgentRuntimePort
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.openai_runtime import OpenAIAgentRuntime
from codelens.review.infrastructure.repositories import (
    SqlCheckpointStore,
    SqlJobQueue,
    SqlReviewStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.review.infrastructure.snapshot_context import FilesystemSnapshotContextAdapter
from codelens.review.infrastructure.transcripts import (
    DeferredTranscriptStore,
    ExecutionTranscriptStore,
)
from codelens.reviewer_catalog.application.prompt_settings import ReviewerPromptSettingsService
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemReviewerPromptStore,
)
from codelens.reviewer_catalog.infrastructure.file_provider_config import (
    FilesystemModelProviderConfigAdapter,
)
from codelens.worker.execution import SqlJobQueuePortAdapter, WorkerReviewExecutor
from codelens.worker.scheduler import ReviewScheduler, WorkerSemaphores
from codelens.worker.singleton import platform_worker_singleton
from codelens.workspace.application.create_snapshot import SnapshotService
from codelens.workspace.application.worktree_lifecycle import (
    ReviewWorktreeLifecycle,
    ReviewWorktreeRecoveryService,
)
from codelens.workspace.infrastructure.change_index import GitChangeIndexBuilder
from codelens.workspace.infrastructure.filesystem_snapshot import FilesystemSnapshotBuilder
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_ignore import GitIgnoreResolver
from codelens.workspace.infrastructure.git_overlay import GitOverlayMaterializer
from codelens.workspace.infrastructure.git_worktrees import (
    GitReviewWorktreeManager,
    RepositoryLockRegistry,
)
from codelens.workspace.infrastructure.input_artifacts import FilesystemInputArtifactStore


@dataclass(frozen=True)
class WorkerComponents:
    """Own Worker resources in the order required for startup and shutdown."""

    settings: Settings
    database: Database
    review_store: SqlReviewStore
    worktree_registry: SqlWorktreeRegistry
    scheduler: ReviewScheduler

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Migrate storage, then let the singleton scheduler recover and claim work."""

        await asyncio.to_thread(self.settings.data_dir.mkdir, parents=True, exist_ok=True)
        await self.database.migrate()
        # Alembic can replace root handlers while loading its migration configuration.
        # Restore the Worker-owned handler before scheduler failures can occur.
        configure_process_logging("worker", data_directory=self.settings.data_dir)
        try:
            await self.scheduler.run(stop_event)
        finally:
            await self.database.dispose()


def build_worker(
    settings: Settings,
    *,
    runtime: AgentRuntimePort | None = None,
) -> WorkerComponents:
    """Compose one independent Worker without relying on API-process memory."""

    database = Database(settings.resolved_database_url)
    review_store = SqlReviewStore(database)
    worktree_registry = SqlWorktreeRegistry(database, settings.data_dir)
    git = GitCli()
    input_artifacts = FilesystemInputArtifactStore(settings.data_dir / "artifacts" / "inputs")
    worktree_manager = GitReviewWorktreeManager(
        data_dir=settings.data_dir,
        git=git,
        registry=worktree_registry,
        locks=RepositoryLockRegistry(),
    )
    lifecycle = ReviewWorktreeLifecycle(
        worktrees=worktree_manager,
        artifacts=input_artifacts,
        materializer=GitOverlayMaterializer(git),
    )
    recovery = ReviewWorktreeRecoveryService(
        lifecycle=lifecycle,
        registry=worktree_registry,
        recovery=worktree_manager,
    )
    snapshot_service = SnapshotService(
        lifecycle=lifecycle,
        manifest_builder=FilesystemSnapshotBuilder(
            git=git,
            ignore=GitIgnoreResolver(git),
        ),
        change_index=GitChangeIndexBuilder(git),
        artifacts=input_artifacts,
        instructions=InstructionResolver(MarkdownInstructionParser()),
        structured_skip=StructuredSkipMatcher(),
    )
    context_adapter = FilesystemSnapshotContextAdapter()
    codec = AgentOutputCodec("1")
    provider_runtime = runtime or OpenAIAgentRuntime(
        FilesystemModelProviderConfigAdapter(settings.data_dir),
        codec,
        git,
    )
    semaphores = WorkerSemaphores.create(
        agent_limit=settings.max_active_agent_runs,
        model_limit=settings.max_active_agent_runs,
        tool_limit=settings.max_active_agent_runs,
    )
    executor = WorkerReviewExecutor(
        settings=settings,
        review_store=review_store,
        worktree_registry=worktree_registry,
        worktree_lifecycle=lifecycle,
        worktree_recovery=recovery,
        snapshot_service=snapshot_service,
        context_builder=ContextBuilder(context_adapter, context_adapter),
        excerpt_reader=context_adapter,
        runtime=provider_runtime,
        output_artifacts=FilesystemRunArtifactStore(
            database,
            settings.data_dir / "artifacts" / "outputs",
        ),
        checkpoints=SqlCheckpointStore(database),
        codec=codec,
        semaphores=semaphores,
        transcripts=DeferredTranscriptStore(
            ExecutionTranscriptStore(settings.data_dir / "artifacts" / "transcripts"),
            settings.data_dir / "runtime" / "transcript-relay.sock",
        ),
        reviewer_prompts=ReviewerPromptSettingsService(
            FilesystemReviewerPromptStore(settings.data_dir), settings.prompt_dir
        ),
    )
    scheduler = ReviewScheduler(
        queue=SqlJobQueuePortAdapter(SqlJobQueue(database)),
        execute=executor.execute,
        singleton=platform_worker_singleton(settings.data_dir),
        recover=executor.recover,
        close=database.dispose,
        semaphores=semaphores,
        max_active_reviews=settings.max_active_reviews,
        poll_min_seconds=0.05,
        poll_max_seconds=1.0,
        record_failure=executor.record_failure,
    )
    return WorkerComponents(
        settings=settings,
        database=database,
        review_store=review_store,
        worktree_registry=worktree_registry,
        scheduler=scheduler,
    )


async def run_worker(settings: Settings, stop_event: asyncio.Event | None = None) -> None:
    """Run one Worker until a termination signal requests structured cancellation."""

    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    if stop_event is None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                continue
            installed.append(signum)
    try:
        await build_worker(settings).run(stop)
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)

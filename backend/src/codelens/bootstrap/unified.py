"""Unified backend process combining API and Worker in one OS process."""

import asyncio
import logging
import signal
from dataclasses import dataclass

import uvicorn

from codelens.bootstrap.logging import configure_process_logging
from codelens.bootstrap.settings import Settings
from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.instruction_policy.application.resolver import InstructionResolver
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser
from codelens.instruction_policy.infrastructure.structured_skip import StructuredSkipMatcher
from codelens.interface.http.app import create_app_with_components
from codelens.interface.http.dependencies import HttpComponents
from codelens.review.application.context_builder import ContextBuilder
from codelens.review.domain.ports import AgentRuntimePort
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.event_bus import InMemoryEventBus
from codelens.review.infrastructure.i18n_prompt_loader import I18nPromptLoader
from codelens.review.infrastructure.model_log import ModelTranscriptLogWriter
from codelens.review.infrastructure.openai_runtime import OpenAIAgentRuntime
from codelens.review.infrastructure.repositories import (
    SqlCheckpointStore,
    SqlEventOutbox,
    SqlJobQueue,
    SqlRecentRepositoryStore,
    SqlReviewStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.review.infrastructure.snapshot_reader import FilesystemSnapshotReader
from codelens.review.infrastructure.transcripts import (
    ExecutionTranscriptStore,
    WorkerTranscriptStore,
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

_LOGGER = logging.getLogger("codelens.bootstrap.unified")


@dataclass(frozen=True)
class UnifiedBackend:
    """Compose API and Worker resources for single-process execution."""

    settings: Settings
    components: HttpComponents
    scheduler: ReviewScheduler

    async def start(self) -> None:
        """Create runtime directories, migrate database, and recover interrupted tasks."""

        await self.components.start()
        configure_process_logging("unified", data_directory=self.settings.data_dir)
        _LOGGER.info("Unified backend started")

    async def run(self, stop: asyncio.Event) -> None:
        """Run HTTP server and scheduler concurrently until stop signal."""

        config = uvicorn.Config(
            create_app_with_components(
                self.settings,
                self.components,
                manage_components=False,
            ),
            host=self.settings.host,
            port=self.settings.port,
            log_config=None,
        )
        server = uvicorn.Server(config)

        async def run_server() -> None:
            await server.serve()

        async def run_scheduler() -> None:
            await self.scheduler.run(stop)

        async def wait_for_stop() -> None:
            await stop.wait()
            server.should_exit = True

        async with asyncio.TaskGroup() as group:
            group.create_task(run_server())
            group.create_task(run_scheduler())
            group.create_task(wait_for_stop())

        _LOGGER.info("Unified backend stopped")

    async def close(self) -> None:
        """Release database connections and other resources."""

        await self.components.close()


def build_unified_backend(
    settings: Settings,
    *,
    runtime: AgentRuntimePort | None = None,
) -> UnifiedBackend:
    """Compose API and Worker with shared database, event bus, and transcripts."""

    database = Database(settings.resolved_database_url)
    event_bus = InMemoryEventBus()
    git = GitCli()

    # Shared infrastructure
    review_store = SqlReviewStore(database, event_bus=event_bus)
    recent_repository_store = SqlRecentRepositoryStore(database)
    worktree_registry = SqlWorktreeRegistry(database, settings.data_dir)
    input_artifacts = FilesystemInputArtifactStore(settings.data_dir / "artifacts" / "inputs")
    transcripts_store = ExecutionTranscriptStore(settings.data_dir / "artifacts" / "transcripts")
    worker_transcripts = WorkerTranscriptStore(
        transcripts_store,
        model_log=ModelTranscriptLogWriter(),
    )

    # Worker components
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
    snapshot_reader = FilesystemSnapshotReader(git)
    codec = AgentOutputCodec("1")
    system_prompts = I18nPromptLoader.load(settings.prompt_dir)
    provider_runtime = runtime or OpenAIAgentRuntime(
        FilesystemModelProviderConfigAdapter(settings.data_dir),
        codec,
        git,
        system_prompts,
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
        context_builder=ContextBuilder(),
        excerpt_reader=snapshot_reader,
        runtime=provider_runtime,
        output_artifacts=FilesystemRunArtifactStore(
            database,
            settings.data_dir / "artifacts" / "outputs",
        ),
        checkpoints=SqlCheckpointStore(database),
        codec=codec,
        semaphores=semaphores,
        transcripts=worker_transcripts,
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
        record_claim=lambda task_id: worker_transcripts.append(
            task_id, "lifecycle", "Review execution started"
        ),
    )

    # API components (sharing event_bus, review_store, worker_transcripts)
    from codelens.review.application.commands import (
        CancelReviewHandler,
        CreateReviewHandler,
        DeleteReviewHandler,
        GetRecentRepositorySettingsHandler,
        GetReviewHandler,
        ListRecentRepositoriesHandler,
        ListReviewsHandler,
        UpdateRecentRepositorySettingsHandler,
    )
    from codelens.review.application.source_preview import FindingSourcePreviewService
    from codelens.reviewer_catalog.application.provider_settings import (
        ModelGatewaySettingsService,
    )
    from codelens.reviewer_catalog.infrastructure.model_gateway_probe import (
        OpenAIModelGatewayProbeAdapter,
    )
    from codelens.workspace.application.browse_directories import BrowseDirectoriesService
    from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
    from codelens.workspace.application.inspect_repository import RepositoryInspector
    from codelens.workspace.application.plan_scope import ScopePlanner
    from codelens.workspace.application.repository_catalog import RepositoryCatalogService
    from codelens.workspace.infrastructure.filesystem_browser import LocalFilesystemBrowserAdapter
    from codelens.workspace.infrastructure.git_overlay import GitReviewInputCaptureAdapter
    from codelens.workspace.infrastructure.git_workspace import GitWorkspaceAdapter
    from codelens.workspace.infrastructure.repository_catalog import GitRepositoryCatalogAdapter
    from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter

    repository_inspector = RepositoryInspector(
        GitRepositoryMetadataAdapter(git),
        settings.repository_roots,
    )
    planner = ScopePlanner(GitWorkspaceAdapter(git))
    capture = ReviewInputCaptureService(GitReviewInputCaptureAdapter(git), input_artifacts)
    provider_config = FilesystemModelProviderConfigAdapter(settings.data_dir)

    components = HttpComponents(
        settings=settings,
        database=database,
        repository_inspector=repository_inspector,
        repository_catalog=RepositoryCatalogService(
            repository_inspector,
            GitRepositoryCatalogAdapter(git),
        ),
        directory_browser=BrowseDirectoriesService(LocalFilesystemBrowserAdapter()),
        create_review=CreateReviewHandler(planner, capture, review_store, input_artifacts),
        get_review=GetReviewHandler(review_store),
        list_reviews=ListReviewsHandler(review_store),
        list_recent_repositories=ListRecentRepositoriesHandler(recent_repository_store),
        get_recent_repository_settings=GetRecentRepositorySettingsHandler(recent_repository_store),
        update_recent_repository_settings=UpdateRecentRepositorySettingsHandler(
            recent_repository_store
        ),
        delete_review=DeleteReviewHandler(
            review_store,
            worktree_registry,
            worktree_manager,
        ),
        cancel_review=CancelReviewHandler(review_store),
        events=SqlEventOutbox(database, event_bus=event_bus),
        event_bus=event_bus,
        review_store=review_store,
        input_artifacts=input_artifacts,
        model_gateways=ModelGatewaySettingsService(
            provider_config, OpenAIModelGatewayProbeAdapter()
        ),
        reviewer_prompts=ReviewerPromptSettingsService(
            FilesystemReviewerPromptStore(settings.data_dir), settings.prompt_dir
        ),
        transcripts=transcripts_store,
        worker_transcripts=worker_transcripts,
        finding_source_preview=FindingSourcePreviewService(review_store, git),
    )

    return UnifiedBackend(
        settings=settings,
        components=components,
        scheduler=scheduler,
    )


async def run_unified(settings: Settings) -> None:
    """Run the unified backend process until a termination signal."""

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signum)

    backend = build_unified_backend(settings)
    try:
        await backend.start()
        await backend.run(stop)
    finally:
        await backend.close()
        for signum in installed:
            loop.remove_signal_handler(signum)

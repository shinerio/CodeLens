"""Unified backend process combining API and Worker in one OS process."""

import asyncio
import logging
import signal
from dataclasses import dataclass

import uvicorn

from codelens.bootstrap.logging import configure_process_logging
from codelens.bootstrap.memory_guard import MemoryGuard
from codelens.bootstrap.settings import Settings
from codelens.bootstrap.web_settings_defaults import load_web_settings_defaults
from codelens.instruction_policy.application.resolver import InstructionResolver
from codelens.instruction_policy.application.settings import InstructionSettingsService
from codelens.instruction_policy.infrastructure.file_settings import (
    FilesystemInstructionLineLimitsStore,
)
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser
from codelens.instruction_policy.infrastructure.structured_skip import StructuredSkipMatcher
from codelens.interface.http.app import create_app_with_components
from codelens.interface.http.dependencies import (
    HttpComponents,
    initialize_plugins,
)
from codelens.plugin.application.export_orchestrator import ExportOrchestrator
from codelens.plugin.application.hook_management import TriggerHookService
from codelens.plugin.application.manual_review_orchestrator import ManualReviewOrchestrator
from codelens.plugin.application.plugin_manager import PluginManager
from codelens.plugin.application.trigger_orchestrator import TriggerOrchestrator
from codelens.plugin.infrastructure.export_history_store import SqliteExportHistoryStore
from codelens.plugin.infrastructure.git_installer import GitPluginInstaller
from codelens.plugin.infrastructure.plugin_loader import CompositePluginLoader
from codelens.plugin.infrastructure.plugin_store import FilesystemPluginStore
from codelens.plugin.report.local_file_export.existing_findings import (
    LocalExistingFindingsProvider,
)
from codelens.plugin.trigger.local_hook.hook_installer import (
    HookInstaller,
)
from codelens.review.application.context_builder import ContextBuilder
from codelens.review.application.review_profiles import (
    CopyReviewProfileHandler,
    CreateReviewProfileHandler,
    DeleteReviewProfileHandler,
    ListReviewProfilesHandler,
    SetDefaultReviewProfileHandler,
    UpdateReviewProfileHandler,
)
from codelens.review.application.settings import (
    ReviewCompletionSettingsService,
    TriggerIdempotencySettingsService,
)
from codelens.review.application.tool_limits_service import ToolLimitsService
from codelens.review.domain.ports import AgentRuntimePort
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.event_bus import InMemoryEventBus
from codelens.review.infrastructure.file_settings import (
    FilesystemReviewCompletionSettingsStore,
    FilesystemTriggerIdempotencySettingsStore,
)
from codelens.review.infrastructure.file_tool_limits import FilesystemToolLimitsStore
from codelens.review.infrastructure.i18n_prompt_loader import I18nPromptLoader
from codelens.review.infrastructure.model_log import ModelTranscriptLogWriter
from codelens.review.infrastructure.openai_runtime import OpenAIAgentRuntime
from codelens.review.infrastructure.repositories import (
    SqlAgentExecutionSpecStore,
    SqlCandidateFindingStore,
    SqlCheckpointStore,
    SqlEventOutbox,
    SqlJobQueue,
    SqlRecentRepositoryStore,
    SqlReviewPlanStore,
    SqlReviewProfileRepository,
    SqlReviewStore,
    SqlVerdictStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.run_artifacts import FilesystemRunArtifactStore
from codelens.review.infrastructure.snapshot_reader import FilesystemSnapshotReader
from codelens.review.infrastructure.transcripts import (
    ExecutionTranscriptStore,
    WorkerTranscriptStore,
)
from codelens.reviewer_catalog.application.prompt_settings import AgentPromptSettingsService
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemAgentPromptStore,
)
from codelens.reviewer_catalog.infrastructure.file_provider_config import (
    FilesystemModelProviderConfigAdapter,
)
from codelens.trigger.application.review_creator_adapter import (
    ReviewCreatorAdapter,
    TriggerRepositoryValidatorAdapter,
)
from codelens.worker.execution import SqlJobQueuePortAdapter, WorkerReviewExecutor
from codelens.worker.scheduler import ReviewScheduler, WorkerSemaphores
from codelens.worker.singleton import platform_worker_singleton
from codelens.workspace.application.create_snapshot import SnapshotService
from codelens.workspace.application.file_exclusion_settings import (
    FileExclusionPolicyService,
)
from codelens.workspace.application.worktree_lifecycle import (
    ReviewWorktreeLifecycle,
    ReviewWorktreeRecoveryService,
)
from codelens.workspace.infrastructure.change_index import GitChangeIndexBuilder
from codelens.workspace.infrastructure.file_exclusion_settings import (
    FilesystemFileExclusionPolicySource,
    FilesystemFileExclusionPolicyStore,
)
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
        await initialize_plugins(self.components)
        configure_process_logging(
            "unified",
            data_directory=self.settings.data_dir,
            default_level=self.components.web_settings_defaults.log_level,
        )
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

    web_settings_defaults = load_web_settings_defaults(
        settings.web_settings_defaults_config
    )
    database = Database(settings.resolved_database_url)
    event_bus = InMemoryEventBus()
    git = GitCli()

    # Shared infrastructure
    review_store = SqlReviewStore(database, event_bus=event_bus)
    event_outbox = SqlEventOutbox(database, event_bus=event_bus)
    recent_repository_store = SqlRecentRepositoryStore(database)
    review_profile_repository = SqlReviewProfileRepository(database)
    worktree_registry = SqlWorktreeRegistry(database, settings.data_dir)
    input_artifacts = FilesystemInputArtifactStore(settings.data_dir / "artifacts" / "inputs")
    transcripts_store = ExecutionTranscriptStore(settings.data_dir / "artifacts" / "transcripts")
    worker_transcripts = WorkerTranscriptStore(
        transcripts_store,
        model_log=ModelTranscriptLogWriter(settings.data_dir),
        rejection_events=event_outbox,
    )
    instruction_line_limits = FilesystemInstructionLineLimitsStore(
        settings.data_dir, web_settings_defaults.instruction_files
    )
    review_completion_settings = ReviewCompletionSettingsService(
        FilesystemReviewCompletionSettingsStore(
            settings.data_dir, web_settings_defaults.review_completion
        )
    )
    trigger_idempotency_settings = TriggerIdempotencySettingsService(
        FilesystemTriggerIdempotencySettingsStore(
            settings.data_dir, web_settings_defaults.trigger_idempotency
        )
    )
    tool_limits_service = ToolLimitsService(
        FilesystemToolLimitsStore(settings.data_dir, web_settings_defaults.tool_limits)
    )
    file_exclusion_source = FilesystemFileExclusionPolicySource(settings.file_exclusion_config)
    file_exclusion_source.get_policy()
    file_exclusion_settings = FileExclusionPolicyService(
        file_exclusion_source,
        FilesystemFileExclusionPolicyStore(
            settings.data_dir, web_settings_defaults.file_exclusions
        ),
    )

    # Create repository inspector early so it can be shared with Worker
    from codelens.workspace.application.inspect_repository import RepositoryInspector
    from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter

    repository_inspector = RepositoryInspector(
        GitRepositoryMetadataAdapter(git),
        settings.repository_roots,
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
        instructions=InstructionResolver(
            MarkdownInstructionParser(),
            line_limits_provider=instruction_line_limits,
        ),
        structured_skip=StructuredSkipMatcher(),
        scope_store=review_store,
    )
    snapshot_reader = FilesystemSnapshotReader(git)
    system_prompts = I18nPromptLoader.load(settings.prompt_dir)
    provider_config_store = FilesystemModelProviderConfigAdapter(settings.data_dir)
    provider_runtime = runtime or OpenAIAgentRuntime(
        provider_config_store,
        git,
        system_prompts,
        completion_settings=review_completion_settings,
        tool_limits_service=tool_limits_service,
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
        semaphores=semaphores,
        transcripts=worker_transcripts,
        agent_prompts=AgentPromptSettingsService(
            FilesystemAgentPromptStore(settings.data_dir), settings.prompt_dir
        ),
        repository_inspector=repository_inspector,
        provider_config=provider_config_store,
        tool_limits_service=tool_limits_service,
        execution_spec_store=SqlAgentExecutionSpecStore(database),
        review_plan_store=SqlReviewPlanStore(database),
        candidate_store=SqlCandidateFindingStore(database),
        verdict_store=SqlVerdictStore(database),
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
        memory_check_interval_seconds=settings.memory_check_interval_seconds,
    )
    # Wire memory guard now that scheduler.active_task_ids() is available; the
    # callbacks evict stale locks/transcripts/subscribers for tasks that are no
    # longer active, then gc.collect() reclaims cyclic references.
    async def _evict_stale_subscribers() -> None:
        await event_bus.evict_stale_subscribers(60.0)

    async def _evict_inactive_transcripts() -> None:
        await worker_transcripts.evict_inactive(scheduler.active_task_ids())

    async def _evict_stale_locks() -> None:
        transcripts_store.evict_locks(scheduler.active_task_ids())

    memory_guard = MemoryGuard(
        limit_bytes=settings.memory_limit_mb * 1024 * 1024,
        cleanup_threshold_ratio=settings.memory_cleanup_threshold_ratio,
        reject_threshold_ratio=settings.memory_reject_threshold_ratio,
        cleanup_callbacks=[
            _evict_stale_subscribers,
            _evict_inactive_transcripts,
            _evict_stale_locks,
        ],
    )
    scheduler.set_memory_guard(memory_guard)

    # API components (sharing event_bus, review_store, worker_transcripts)
    from codelens.review.application.commands import (
        CancelReviewHandler,
        CreateReviewHandler,
        DeleteRecentRepositoryHandler,
        DeleteReviewHandler,
        GetRecentRepositorySettingsHandler,
        GetReviewHandler,
        ListRecentRepositoriesHandler,
        ListReviewsHandler,
        RetryReviewHandler,
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

    planner = ScopePlanner(GitWorkspaceAdapter(git))
    capture = ReviewInputCaptureService(GitReviewInputCaptureAdapter(git), input_artifacts)
    provider_config = FilesystemModelProviderConfigAdapter(settings.data_dir)
    tool_limits = ToolLimitsService(
        FilesystemToolLimitsStore(settings.data_dir, web_settings_defaults.tool_limits)
    )

    # Plugin context: store, loader, lifecycle manager, and export orchestrator.
    # The terminal hook is late-bound on the review store so that the
    # orchestrator (which depends on the store) can wire itself after
    # construction without circular references.
    plugins_dir = settings.data_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_store = FilesystemPluginStore(settings.data_dir)
    plugin_installer = GitPluginInstaller(git, plugins_dir)
    plugin_loader = CompositePluginLoader()
    plugin_manager = PluginManager(plugin_store, plugin_installer, plugins_dir, plugin_loader)
    local_existing_findings = LocalExistingFindingsProvider(plugin_store)
    export_history = SqliteExportHistoryStore(settings.data_dir / "codelens.sqlite3")
    export_orchestrator = ExportOrchestrator(
        review_store,
        git,
        plugin_store,
        plugin_loader,
        export_history,
        SqlReviewPlanStore(database),
        SqlCheckpointStore(database),
    )

    async def _terminal_export_hook(task_id: str, _status: str) -> None:
        """Adapter for SqlReviewStore.terminal_hook → ExportOrchestrator.

        SqlReviewStore fires the terminal hook with (task_id, status); the
        export orchestrator only needs task_id. Failures are isolated inside
        ``auto_export_if_enabled`` and by ``_fire_terminal_hook``.
        """

        await export_orchestrator.auto_export_if_enabled(task_id)

    review_store.set_terminal_hook(_terminal_export_hook)

    # Trigger orchestrator components
    from pathlib import Path

    hook_installer = HookInstaller(
        Path(__file__).parent.parent / "plugin" / "trigger" / "local_hook"
    )
    review_creator_adapter = ReviewCreatorAdapter(
        CreateReviewHandler(
            planner,
            capture,
            review_store,
            input_artifacts,
            idempotency_settings=trigger_idempotency_settings,
            file_exclusion_settings=file_exclusion_settings,
            existing_findings_provider=local_existing_findings,
        ),
        repository_inspector,
    )
    trigger_orchestrator = TriggerOrchestrator(plugin_store, review_creator_adapter, plugin_loader)
    manual_review_orchestrator = ManualReviewOrchestrator(
        plugin_store, review_creator_adapter, plugin_loader
    )
    trigger_hooks = TriggerHookService(
        plugin_manager,
        hook_installer,
        TriggerRepositoryValidatorAdapter(repository_inspector),
        settings.port,
    )

    components = HttpComponents(
        settings=settings,
        web_settings_defaults=web_settings_defaults,
        database=database,
        repository_inspector=repository_inspector,
        repository_catalog=RepositoryCatalogService(
            repository_inspector,
            GitRepositoryCatalogAdapter(git),
        ),
        directory_browser=BrowseDirectoriesService(LocalFilesystemBrowserAdapter()),
        create_review=CreateReviewHandler(
            planner,
            capture,
            review_store,
            input_artifacts,
            file_exclusion_settings=file_exclusion_settings,
            existing_findings_provider=local_existing_findings,
        ),
        get_review=GetReviewHandler(review_store),
        list_reviews=ListReviewsHandler(review_store),
        list_recent_repositories=ListRecentRepositoriesHandler(recent_repository_store),
        delete_recent_repository=DeleteRecentRepositoryHandler(recent_repository_store),
        get_recent_repository_settings=GetRecentRepositorySettingsHandler(recent_repository_store),
        update_recent_repository_settings=UpdateRecentRepositorySettingsHandler(
            recent_repository_store
        ),
        create_review_profile=CreateReviewProfileHandler(review_profile_repository),
        update_review_profile=UpdateReviewProfileHandler(review_profile_repository),
        copy_review_profile=CopyReviewProfileHandler(review_profile_repository),
        delete_review_profile=DeleteReviewProfileHandler(review_profile_repository),
        set_default_review_profile=SetDefaultReviewProfileHandler(review_profile_repository),
        list_review_profiles=ListReviewProfilesHandler(review_profile_repository),
        instruction_settings=InstructionSettingsService(instruction_line_limits),
        review_completion_settings=review_completion_settings,
        trigger_idempotency_settings=trigger_idempotency_settings,
        delete_review=DeleteReviewHandler(
            review_store,
            worktree_registry,
            worktree_manager,
        ),
        cancel_review=CancelReviewHandler(review_store, cancel_task=scheduler.cancel_task),
        retry_review=RetryReviewHandler(review_store),
        events=event_outbox,
        event_bus=event_bus,
        review_store=review_store,
        review_plan_store=SqlReviewPlanStore(database),
        checkpoints=SqlCheckpointStore(database),
        verdict_store=SqlVerdictStore(database),
        input_artifacts=input_artifacts,
        model_gateways=ModelGatewaySettingsService(
            provider_config, OpenAIModelGatewayProbeAdapter()
        ),
        agent_prompts=AgentPromptSettingsService(
            FilesystemAgentPromptStore(settings.data_dir), settings.prompt_dir
        ),
        transcripts=transcripts_store,
        worker_transcripts=worker_transcripts,
        finding_source_preview=FindingSourcePreviewService(review_store, git, input_artifacts, git),
        plugin_manager=plugin_manager,
        export_orchestrator=export_orchestrator,
        export_history=export_history,
        trigger_orchestrator=trigger_orchestrator,
        manual_review_orchestrator=manual_review_orchestrator,
        hook_installer=hook_installer,
        trigger_hooks=trigger_hooks,
        tool_limits=tool_limits,
        file_exclusion_settings=file_exclusion_settings,
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

import asyncio
from dataclasses import dataclass

from fastapi import Request

from codelens.bootstrap.settings import Settings
from codelens.instruction_policy.application.settings import InstructionSettingsService
from codelens.instruction_policy.infrastructure.file_settings import (
    FilesystemInstructionLineLimitsStore,
)
from codelens.plugin.application.export_orchestrator import ExportOrchestrator
from codelens.plugin.application.hook_management import TriggerHookService
from codelens.plugin.application.plugin_manager import PluginManager
from codelens.plugin.application.trigger_orchestrator import TriggerOrchestrator
from codelens.plugin.infrastructure.git_installer import GitPluginInstaller
from codelens.plugin.infrastructure.plugin_loader import CompositePluginLoader
from codelens.plugin.infrastructure.plugin_store import FilesystemPluginStore
from codelens.plugin.trigger.local_hook.hook_installer import (
    HookInstaller,
)
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
from codelens.review.application.settings import ReviewCompletionSettingsService
from codelens.review.application.source_preview import FindingSourcePreviewService
from codelens.review.application.tool_limits_service import ToolLimitsService
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.event_bus import InMemoryEventBus
from codelens.review.infrastructure.file_settings import FilesystemReviewCompletionSettingsStore
from codelens.review.infrastructure.file_tool_limits import FilesystemToolLimitsStore
from codelens.review.infrastructure.repositories import (
    SqlEventOutbox,
    SqlRecentRepositoryStore,
    SqlReviewStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.transcripts import (
    ExecutionTranscriptStore,
    WorkerTranscriptStore,
)
from codelens.reviewer_catalog.application.prompt_settings import ReviewerPromptSettingsService
from codelens.reviewer_catalog.application.provider_settings import (
    ModelGatewaySettingsService,
)
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemReviewerPromptStore,
)
from codelens.reviewer_catalog.infrastructure.file_provider_config import (
    FilesystemModelProviderConfigAdapter,
)
from codelens.reviewer_catalog.infrastructure.model_gateway_probe import (
    OpenAIModelGatewayProbeAdapter,
)
from codelens.trigger.application.review_creator_adapter import (
    ReviewCreatorAdapter,
    TriggerRepositoryValidatorAdapter,
)
from codelens.workspace.application.browse_directories import BrowseDirectoriesService
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.inspect_repository import RepositoryInspector
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.application.repository_catalog import RepositoryCatalogService
from codelens.workspace.infrastructure.filesystem_browser import LocalFilesystemBrowserAdapter
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_overlay import GitReviewInputCaptureAdapter
from codelens.workspace.infrastructure.git_workspace import GitWorkspaceAdapter
from codelens.workspace.infrastructure.git_worktrees import (
    GitReviewWorktreeManager,
    RepositoryLockRegistry,
)
from codelens.workspace.infrastructure.input_artifacts import FilesystemInputArtifactStore
from codelens.workspace.infrastructure.repository_catalog import GitRepositoryCatalogAdapter
from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter


@dataclass(frozen=True)
class HttpComponents:
    """Hold interface dependencies while keeping construction at the outermost layer."""

    settings: Settings
    database: Database
    repository_inspector: RepositoryInspector
    repository_catalog: RepositoryCatalogService
    directory_browser: BrowseDirectoriesService
    create_review: CreateReviewHandler
    get_review: GetReviewHandler
    list_reviews: ListReviewsHandler
    list_recent_repositories: ListRecentRepositoriesHandler
    delete_recent_repository: DeleteRecentRepositoryHandler
    get_recent_repository_settings: GetRecentRepositorySettingsHandler
    update_recent_repository_settings: UpdateRecentRepositorySettingsHandler
    instruction_settings: InstructionSettingsService
    review_completion_settings: ReviewCompletionSettingsService
    tool_limits: ToolLimitsService
    delete_review: DeleteReviewHandler
    cancel_review: CancelReviewHandler
    retry_review: RetryReviewHandler
    events: SqlEventOutbox
    event_bus: InMemoryEventBus
    review_store: SqlReviewStore
    input_artifacts: FilesystemInputArtifactStore
    model_gateways: ModelGatewaySettingsService
    reviewer_prompts: ReviewerPromptSettingsService
    transcripts: ExecutionTranscriptStore
    worker_transcripts: WorkerTranscriptStore
    finding_source_preview: FindingSourcePreviewService
    plugin_manager: PluginManager
    export_orchestrator: ExportOrchestrator
    trigger_orchestrator: TriggerOrchestrator
    hook_installer: HookInstaller
    trigger_hooks: TriggerHookService

    async def start(self) -> None:
        """Create contained runtime directories and apply migrations before serving."""

        await asyncio.to_thread(self.settings.data_dir.mkdir, parents=True, exist_ok=True)
        await self.database.migrate()
        references = await self.review_store.list_input_artifact_references()
        await self.input_artifacts.prune_orphans(references)

    async def close(self) -> None:
        """Close database resources after streaming responses and requests stop."""

        await self.database.dispose()


def build_components(settings: Settings) -> HttpComponents:
    """Compose application services with concrete outer adapters."""

    database = Database(settings.resolved_database_url)
    event_bus = InMemoryEventBus()
    git = GitCli()
    repository_inspector = RepositoryInspector(
        GitRepositoryMetadataAdapter(git),
        settings.repository_roots,
    )
    planner = ScopePlanner(GitWorkspaceAdapter(git))
    input_artifacts = FilesystemInputArtifactStore(settings.data_dir / "artifacts" / "inputs")
    capture = ReviewInputCaptureService(GitReviewInputCaptureAdapter(git), input_artifacts)
    review_store = SqlReviewStore(database, event_bus=event_bus)
    recent_repository_store = SqlRecentRepositoryStore(database)
    worktree_registry = SqlWorktreeRegistry(database, settings.data_dir)
    worktree_manager = GitReviewWorktreeManager(
        data_dir=settings.data_dir,
        git=git,
        registry=worktree_registry,
        locks=RepositoryLockRegistry(),
    )
    provider_config = FilesystemModelProviderConfigAdapter(settings.data_dir)
    instruction_line_limits = FilesystemInstructionLineLimitsStore(settings.data_dir)
    review_completion_settings = ReviewCompletionSettingsService(
        FilesystemReviewCompletionSettingsStore(settings.data_dir)
    )
    tool_limits = ToolLimitsService(FilesystemToolLimitsStore(settings.data_dir))
    transcripts = ExecutionTranscriptStore(settings.data_dir / "artifacts" / "transcripts")
    worker_transcripts = WorkerTranscriptStore(transcripts)
    plugins_dir = settings.data_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_store = FilesystemPluginStore(settings.data_dir)
    plugin_installer = GitPluginInstaller(git, plugins_dir)
    plugin_loader = CompositePluginLoader()
    plugin_manager = PluginManager(
        plugin_store, plugin_installer, plugins_dir, plugin_loader
    )
    export_orchestrator = ExportOrchestrator(
        review_store,
        git,
        plugin_store,
        plugin_loader,
    )

    async def _terminal_export_hook(task_id: str, _status: str) -> None:
        """Adapter for SqlReviewStore.terminal_hook → ExportOrchestrator."""
        await export_orchestrator.auto_export_if_enabled(task_id)

    review_store.set_terminal_hook(_terminal_export_hook)

    # Unified plugin components
    from pathlib import Path

    hook_installer = HookInstaller(
        Path(__file__).parent.parent.parent / "plugin" / "trigger" / "local_hook"
    )
    review_creator_adapter = ReviewCreatorAdapter(
        CreateReviewHandler(planner, capture, review_store, input_artifacts),
        repository_inspector,
    )
    trigger_orchestrator = TriggerOrchestrator(
        plugin_store, review_creator_adapter, plugin_loader
    )
    trigger_hooks = TriggerHookService(
        plugin_manager,
        hook_installer,
        TriggerRepositoryValidatorAdapter(repository_inspector),
        settings.port,
    )

    return HttpComponents(
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
        delete_recent_repository=DeleteRecentRepositoryHandler(recent_repository_store),
        get_recent_repository_settings=GetRecentRepositorySettingsHandler(recent_repository_store),
        update_recent_repository_settings=UpdateRecentRepositorySettingsHandler(
            recent_repository_store
        ),
        instruction_settings=InstructionSettingsService(instruction_line_limits),
        review_completion_settings=review_completion_settings,
        tool_limits=tool_limits,
        delete_review=DeleteReviewHandler(
            review_store,
            worktree_registry,
            worktree_manager,
        ),
        cancel_review=CancelReviewHandler(review_store),
        retry_review=RetryReviewHandler(review_store),
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
        transcripts=transcripts,
        worker_transcripts=worker_transcripts,
        finding_source_preview=FindingSourcePreviewService(review_store, git),
        plugin_manager=plugin_manager,
        export_orchestrator=export_orchestrator,
        trigger_orchestrator=trigger_orchestrator,
        hook_installer=hook_installer,
        trigger_hooks=trigger_hooks,
    )


async def initialize_plugins(components: HttpComponents) -> None:
    """Initialize built-in plugins and add their paths to trusted roots."""

    await components.plugin_manager.initialize_builtin()
    # Add plugin install paths to trusted repository roots
    from codelens.plugin.infrastructure.plugin_store import FilesystemPluginStore
    plugin_store = FilesystemPluginStore(components.settings.data_dir)
    plugins = await plugin_store.list_plugins()
    for plugin in plugins:
        if plugin.install_path and (plugin.trigger_enabled or plugin.report_enabled):
            components.repository_inspector.add_root(plugin.install_path)


def get_components(request: Request) -> HttpComponents:
    """Return the application-scoped dependency container."""

    components: HttpComponents = request.app.state.components
    return components


class HttpProblem(Exception):
    """Carry a stable path-free HTTP failure from a route to the app boundary."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

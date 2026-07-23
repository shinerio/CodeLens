import asyncio
from dataclasses import dataclass

from fastapi import Request

from codelens.bootstrap.settings import Settings
from codelens.review.application.commands import (
    CancelReviewHandler,
    CreateReviewHandler,
    DeleteReviewHandler,
    GetReviewHandler,
    ListReviewsHandler,
)
from codelens.review.application.source_preview import FindingSourcePreviewService
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import (
    SqlEventOutbox,
    SqlReviewStore,
    SqlWorktreeRegistry,
)
from codelens.review.infrastructure.transcripts import (
    ExecutionTranscriptStore,
    UnixWorkerTranscriptQueryClient,
)
from codelens.reviewer_catalog.application.prompt_settings import ReviewerPromptSettingsService
from codelens.reviewer_catalog.application.provider_settings import (
    GetProviderSettingsHandler,
    ModelGatewaySettingsService,
    UpdateProviderSettingsHandler,
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
    delete_review: DeleteReviewHandler
    cancel_review: CancelReviewHandler
    events: SqlEventOutbox
    review_store: SqlReviewStore
    input_artifacts: FilesystemInputArtifactStore
    get_provider_settings: GetProviderSettingsHandler
    update_provider_settings: UpdateProviderSettingsHandler
    model_gateways: ModelGatewaySettingsService
    reviewer_prompts: ReviewerPromptSettingsService
    transcripts: ExecutionTranscriptStore
    worker_transcripts: UnixWorkerTranscriptQueryClient
    finding_source_preview: FindingSourcePreviewService

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
    git = GitCli()
    repository_inspector = RepositoryInspector(
        GitRepositoryMetadataAdapter(git),
        settings.repository_roots,
    )
    planner = ScopePlanner(GitWorkspaceAdapter(git))
    input_artifacts = FilesystemInputArtifactStore(settings.data_dir / "artifacts" / "inputs")
    capture = ReviewInputCaptureService(GitReviewInputCaptureAdapter(git), input_artifacts)
    review_store = SqlReviewStore(database)
    worktree_registry = SqlWorktreeRegistry(database, settings.data_dir)
    worktree_manager = GitReviewWorktreeManager(
        data_dir=settings.data_dir,
        git=git,
        registry=worktree_registry,
        locks=RepositoryLockRegistry(),
    )
    provider_config = FilesystemModelProviderConfigAdapter(settings.data_dir)
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
        delete_review=DeleteReviewHandler(
            review_store,
            worktree_registry,
            worktree_manager,
        ),
        cancel_review=CancelReviewHandler(review_store),
        events=SqlEventOutbox(database),
        review_store=review_store,
        input_artifacts=input_artifacts,
        get_provider_settings=GetProviderSettingsHandler(provider_config),
        update_provider_settings=UpdateProviderSettingsHandler(provider_config),
        model_gateways=ModelGatewaySettingsService(
            provider_config, OpenAIModelGatewayProbeAdapter()
        ),
        reviewer_prompts=ReviewerPromptSettingsService(
            FilesystemReviewerPromptStore(settings.data_dir), settings.prompt_dir
        ),
        transcripts=ExecutionTranscriptStore(settings.data_dir / "artifacts" / "transcripts"),
        worker_transcripts=UnixWorkerTranscriptQueryClient(
            settings.data_dir / "runtime" / "worker-transcripts.sock"
        ),
        finding_source_preview=FindingSourcePreviewService(review_store, git),
    )


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

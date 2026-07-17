import asyncio
from dataclasses import dataclass

from fastapi import Request

from codelens.bootstrap.settings import Settings
from codelens.review.application.commands import (
    CancelReviewHandler,
    CreateReviewHandler,
    GetReviewHandler,
)
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import SqlEventOutbox, SqlReviewStore
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.inspect_repository import RepositoryInspector
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_overlay import GitReviewInputCaptureAdapter
from codelens.workspace.infrastructure.git_workspace import GitWorkspaceAdapter
from codelens.workspace.infrastructure.input_artifacts import FilesystemInputArtifactStore
from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter


@dataclass(frozen=True)
class HttpComponents:
    """Hold interface dependencies while keeping construction at the outermost layer."""

    settings: Settings
    database: Database
    repository_inspector: RepositoryInspector
    create_review: CreateReviewHandler
    get_review: GetReviewHandler
    cancel_review: CancelReviewHandler
    events: SqlEventOutbox
    review_store: SqlReviewStore
    input_artifacts: FilesystemInputArtifactStore

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
    return HttpComponents(
        settings=settings,
        database=database,
        repository_inspector=repository_inspector,
        create_review=CreateReviewHandler(planner, capture, review_store, input_artifacts),
        get_review=GetReviewHandler(review_store),
        cancel_review=CancelReviewHandler(review_store),
        events=SqlEventOutbox(database),
        review_store=review_store,
        input_artifacts=input_artifacts,
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

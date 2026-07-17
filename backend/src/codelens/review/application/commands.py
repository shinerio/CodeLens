import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import ReviewRecord, ReviewStorePort
from codelens.shared.domain.errors import DomainError
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.domain.models import ReviewScope
from codelens.workspace.domain.ports import InputArtifactPort, RepositoryInfo


class ReviewNotFoundError(DomainError):
    """Raised when a path-safe task ID has no durable ReviewTask."""

    code = "review_not_found"


@dataclass(frozen=True)
class CreateReviewCommand:
    """Carry only validated repository metadata and public review selections."""

    repository: RepositoryInfo
    scope: ReviewScope
    selected_agent_versions: tuple[str, ...]


class CreateReviewHandler:
    """Pin mutable refs and capture dirty input before creating a durable command."""

    def __init__(
        self,
        planner: ScopePlanner,
        capture: ReviewInputCaptureService,
        store: ReviewStorePort,
        input_artifacts: InputArtifactPort,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._planner = planner
        self._capture = capture
        self._store = store
        self._input_artifacts = input_artifacts
        self._id_factory = id_factory or (lambda: f"review_{uuid.uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))

    async def handle(self, command: CreateReviewCommand) -> ReviewRecord:
        """Create a task only after all mutable repository input is frozen."""

        scope_plan = await self._planner.plan(command.repository.path, command.scope)
        captured = await self._capture.capture(command.repository.path, scope_plan)
        artifact = captured.overlay_artifact
        task = ReviewTask.create(
            task_id=self._id_factory(),
            repository_id=command.repository.repository_id,
            repository_realpath_hash=command.repository.repository_realpath_hash,
            git_common_dir_hash=command.repository.git_common_dir_hash,
            scope=command.scope,
            target=captured.target,
            repository_path=command.repository.path,
            target_paths=scope_plan.target_paths,
            selected_agent_versions=command.selected_agent_versions,
            created_at=self._clock(),
            overlay_artifact_ref=artifact.reference if artifact is not None else None,
        )
        try:
            await self._store.create_with_job(task)
        except BaseException:
            if artifact is not None:
                await self._input_artifacts.discard(artifact.reference)
            raise
        record = await self._store.get_review(task.task_id)
        if record is None:
            raise RuntimeError("persisted ReviewTask could not be reloaded")
        return record


class GetReviewHandler:
    """Load path-free ReviewTask summaries through an application boundary."""

    def __init__(self, store: ReviewStorePort) -> None:
        self._store = store

    async def handle(self, task_id: str) -> ReviewRecord:
        record = await self._store.get_review(task_id)
        if record is None:
            raise ReviewNotFoundError("review does not exist")
        return record


class CancelReviewHandler:
    """Persist cancellation intent without directly terminating Worker execution."""

    def __init__(self, store: ReviewStorePort) -> None:
        self._store = store

    async def handle(self, task_id: str) -> ReviewRecord:
        record = await self._store.request_cancellation(task_id)
        if record is None:
            raise ReviewNotFoundError("review does not exist")
        return record

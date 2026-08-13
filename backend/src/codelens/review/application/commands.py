import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from codelens.findings.domain.existing_findings import ExistingFinding
from codelens.review.application.settings import TriggerIdempotencySettingsService
from codelens.review.domain.agent_run import InvalidAgentRunStateError
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import (
    RecentRepositoryRecord,
    RecentRepositoryStorePort,
    ReviewRecord,
    ReviewStorePort,
)
from codelens.review.domain.review_strategy import FixedReviewerSelection, ReviewProfileSnapshot
from codelens.shared.domain.errors import DomainError
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.file_exclusion_settings import (
    ReviewFileExclusionPolicyProviderPort,
)
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.domain.models import ReviewScope, UncommittedScope
from codelens.workspace.domain.ports import (
    InputArtifactPort,
    RepositoryInfo,
    ReviewWorktreePort,
    WorktreeRegistryPort,
)
from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy

_LOGGER = logging.getLogger("codelens.review.commands")


class ReviewNotFoundError(DomainError):
    """Raised when a path-safe task ID has no durable ReviewTask."""

    code = "review_not_found"


class ExistingFindingsProviderPort(Protocol):
    """Load structured historical issues before a Review task is frozen."""

    async def load(self, repository_path: Path) -> tuple[ExistingFinding, ...]: ...


@dataclass(frozen=True)
class CreateReviewCommand:
    """Carry only validated repository metadata and public review selections."""

    repository: RepositoryInfo
    scope: ReviewScope
    review_profile: ReviewProfileSnapshot
    trigger_source: Literal["manual", "plugin"] = "manual"
    supersede_policy: Literal["latest_snapshot", "preserve_all"] | None = None
    prompt_locale: str = "en"
    external_context: dict | None = None
    skip_if_duplicate: bool = False
    existing_findings: tuple[ExistingFinding, ...] = ()


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
        idempotency_settings: TriggerIdempotencySettingsService | None = None,
        file_exclusion_settings: ReviewFileExclusionPolicyProviderPort | None = None,
        existing_findings_provider: ExistingFindingsProviderPort | None = None,
    ) -> None:
        self._planner = planner
        self._capture = capture
        self._store = store
        self._input_artifacts = input_artifacts
        self._id_factory = id_factory or (lambda: f"review_{uuid.uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._idempotency_settings = idempotency_settings
        self._file_exclusion_settings = file_exclusion_settings
        self._existing_findings_provider = existing_findings_provider

    async def handle(self, command: CreateReviewCommand) -> ReviewRecord:
        """Create a task only after all mutable repository input is frozen."""

        _LOGGER.debug("Planning review scope", extra={"scope_type": type(command.scope).__name__})
        scope_plan = await self._planner.plan(command.repository.path, command.scope)
        _LOGGER.info(
            "Review scope planned",
            extra={"target_path_count": len(scope_plan.candidate_paths)},
        )
        captured = await self._capture.capture(command.repository.path, scope_plan)
        artifact = captured.overlay_artifact

        if (
            command.skip_if_duplicate
            and self._idempotency_settings is not None
            and not isinstance(command.scope, UncommittedScope)
        ):
            settings = await self._idempotency_settings.get()
            if settings.enabled:
                existing = await self._store.find_duplicate_review(
                    repository_id=command.repository.repository_id,
                    base_oid=captured.target.base_oid,
                    head_oid=captured.target.head_oid,
                )
                if existing is not None:
                    if artifact is not None:
                        await self._input_artifacts.discard(artifact.reference)
                    _LOGGER.info(
                        "Duplicate triggered review skipped",
                        extra={
                            "existing_task_id": existing.task_id,
                            "repository_id": command.repository.repository_id,
                            "base_oid": captured.target.base_oid,
                            "head_oid": captured.target.head_oid,
                        },
                    )
                    return existing

        file_exclusion_policy = (
            await self._file_exclusion_settings.get()
            if self._file_exclusion_settings is not None
            else ReviewFileExclusionPolicy()
        )
        try:
            provided_existing_findings = (
                await self._existing_findings_provider.load(command.repository.path)
                if self._existing_findings_provider is not None
                else ()
            )
            task = ReviewTask.create(
                task_id=self._id_factory(),
                repository_id=command.repository.repository_id,
                repository_realpath_hash=command.repository.repository_realpath_hash,
                git_common_dir_hash=command.repository.git_common_dir_hash,
                scope=command.scope,
                target=captured.target,
                repository_path=command.repository.path,
                candidate_paths=scope_plan.candidate_paths,
                selected_agent_versions=(
                    command.review_profile.reviewer_selection.reviewer_versions
                    if isinstance(
                        command.review_profile.reviewer_selection,
                        FixedReviewerSelection,
                    )
                    else ()
                ),
                review_profile=command.review_profile,
                trigger_source=command.trigger_source,
                supersede_policy=command.supersede_policy,
                prompt_locale=command.prompt_locale,
                created_at=self._clock(),
                overlay_artifact_ref=artifact.reference if artifact is not None else None,
                external_context=command.external_context,
                existing_findings=(*provided_existing_findings, *command.existing_findings),
                file_exclusion_policy=file_exclusion_policy,
            )
        except BaseException:
            if artifact is not None:
                await self._input_artifacts.discard(artifact.reference)
            raise
        try:
            await self._store.create_with_job(task)
        except BaseException:
            _LOGGER.exception("Review persistence failed", extra={"task_id": task.task_id})
            if artifact is not None:
                await self._input_artifacts.discard(artifact.reference)
            raise
        record = await self._store.get_review(task.task_id)
        if record is None:
            _LOGGER.error("Persisted review could not be reloaded", extra={"task_id": task.task_id})
            raise RuntimeError("persisted ReviewTask could not be reloaded")
        _LOGGER.info("Review task persisted", extra={"task_id": task.task_id})
        return record


class GetReviewHandler:
    """Load path-free ReviewTask summaries through an application boundary."""

    def __init__(self, store: ReviewStorePort) -> None:
        self._store = store

    async def handle(self, task_id: str) -> ReviewRecord:
        record = await self._store.get_review(task_id)
        if record is None or record.is_deleted:
            raise ReviewNotFoundError("review does not exist")
        return record


class ListReviewsHandler:
    """List persistent visible Review workspaces through an application boundary."""

    def __init__(self, store: ReviewStorePort) -> None:
        self._store = store

    async def handle(self) -> tuple[ReviewRecord, ...]:
        """Return newest Review workspaces for the navigation hierarchy."""

        return await self._store.list_reviews()


class RetryReviewHandler:
    """Create a new durable Review from a failed task's frozen request."""

    def __init__(
        self,
        store: ReviewStorePort,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._id_factory = id_factory or (lambda: f"review_{uuid.uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))

    async def handle(self, task_id: str) -> ReviewRecord:
        """Keep the failed task immutable and enqueue an independent retry task."""

        source = await self._store.get_review(task_id)
        if source is None or source.is_deleted:
            raise ReviewNotFoundError("review does not exist")
        if source.status != "failed":
            raise InvalidAgentRunStateError("only failed reviews can retry")
        record = await self._store.retry_failed_review(
            task_id,
            self._id_factory(),
            self._clock(),
        )
        if record is None:
            raise ReviewNotFoundError("review does not exist")
        return record


class ListRecentRepositoriesHandler:
    """List repository directories from the independent recent-use catalog."""

    def __init__(self, store: RecentRepositoryStorePort) -> None:
        self._store = store

    async def handle(self) -> tuple[RecentRepositoryRecord, ...]:
        """Return the configured newest-first list for repository selection."""

        limit = await self._store.get_limit()
        return await self._store.list_recent_repositories(limit)


class DeleteRecentRepositoryHandler:
    """Remove one repository shortcut without changing any Review workspace."""

    def __init__(self, store: RecentRepositoryStorePort) -> None:
        self._store = store

    async def handle(self, repository_path: Path) -> None:
        """Idempotently remove the exact persisted recent repository entry."""

        await self._store.delete_recent_repository(repository_path)


class GetRecentRepositorySettingsHandler:
    """Read the persisted recent repository list setting."""

    def __init__(self, store: RecentRepositoryStorePort) -> None:
        self._store = store

    async def handle(self) -> int:
        """Return the current LRU capacity."""

        return await self._store.get_limit()


class UpdateRecentRepositorySettingsHandler:
    """Update the recent repository LRU capacity and apply it immediately."""

    def __init__(self, store: RecentRepositoryStorePort) -> None:
        self._store = store

    async def handle(self, limit: int) -> int:
        """Persist one validated capacity through the repository catalog boundary."""

        return await self._store.update_limit(limit)


class DeleteReviewHandler:
    """Tombstone a Review and remove only its verified owned worktree."""

    def __init__(
        self,
        store: ReviewStorePort,
        worktree_registry: WorktreeRegistryPort,
        worktrees: ReviewWorktreePort,
    ) -> None:
        self._store = store
        self._worktree_registry = worktree_registry
        self._worktrees = worktrees

    async def handle(self, task_id: str) -> None:
        """Request cancellation, then remove a scoped checkout when one exists."""

        if not await self._store.soft_delete_review(task_id):
            raise ReviewNotFoundError("review does not exist")
        worktree = await self._worktree_registry.get(task_id)
        if worktree is not None:
            await self._worktrees.remove_owned(worktree)


class CancelReviewHandler:
    """Persist cancellation intent and actively terminate Worker execution."""

    def __init__(
        self,
        store: ReviewStorePort,
        cancel_task: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._cancel_task = cancel_task

    async def handle(self, task_id: str) -> ReviewRecord:
        record = await self._store.request_cancellation(task_id)
        if record is None:
            raise ReviewNotFoundError("review does not exist")
        if self._cancel_task is not None:
            self._cancel_task(task_id)
        return record

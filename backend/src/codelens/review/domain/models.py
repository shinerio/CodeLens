from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from codelens.shared.domain.errors import DomainError
from codelens.workspace.domain.models import ReviewScope, ReviewTarget


class InvalidReviewStateError(DomainError):
    """Raised when a ReviewTask transition violates its forward-only state machine."""

    code = "invalid_review_state"


class ReviewStatus(StrEnum):
    """Stable ReviewTask lifecycle states persisted and exposed by API DTOs."""

    CREATED = "created"
    PROVISIONING_WORKTREE = "provisioning_worktree"
    SNAPSHOTTING = "snapshotting"
    PREPARING = "preparing"
    REVIEWING = "reviewing"
    VALIDATING = "validating"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELED = "canceled"


_HAPPY_PATH = (
    ReviewStatus.CREATED,
    ReviewStatus.PROVISIONING_WORKTREE,
    ReviewStatus.SNAPSHOTTING,
    ReviewStatus.PREPARING,
    ReviewStatus.REVIEWING,
    ReviewStatus.VALIDATING,
    ReviewStatus.SYNTHESIZING,
    ReviewStatus.COMPLETED,
)
_TERMINAL = {
    ReviewStatus.COMPLETED,
    ReviewStatus.PARTIAL,
    ReviewStatus.FAILED,
    ReviewStatus.CANCELED,
}
_ALLOWED_TRANSITIONS = {
    ReviewStatus.CREATED: {ReviewStatus.PROVISIONING_WORKTREE},
    ReviewStatus.PROVISIONING_WORKTREE: {ReviewStatus.SNAPSHOTTING},
    ReviewStatus.SNAPSHOTTING: {ReviewStatus.PREPARING},
    ReviewStatus.PREPARING: {ReviewStatus.REVIEWING},
    ReviewStatus.REVIEWING: {ReviewStatus.VALIDATING},
    ReviewStatus.VALIDATING: {ReviewStatus.SYNTHESIZING},
    ReviewStatus.SYNTHESIZING: {ReviewStatus.COMPLETED, ReviewStatus.PARTIAL},
}


@dataclass
class ReviewTask:
    """Enforce the worktree-first Review lifecycle and cancellation invariant."""

    task_id: str
    repository_id: str
    repository_realpath_hash: str
    git_common_dir_hash: str
    scope: ReviewScope
    target: ReviewTarget
    selected_agent_versions: tuple[str, ...]
    created_at: datetime
    overlay_artifact_ref: str | None = None
    worktree_id: str | None = None
    snapshot_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancellation_requested: bool = False
    _status: ReviewStatus = field(default=ReviewStatus.CREATED, init=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        repository_id: str,
        repository_realpath_hash: str,
        git_common_dir_hash: str,
        scope: ReviewScope,
        target: ReviewTarget,
        selected_agent_versions: tuple[str, ...],
        created_at: datetime,
        overlay_artifact_ref: str | None = None,
    ) -> "ReviewTask":
        """Create a task only when at least one immutable Agent version is selected."""

        if not selected_agent_versions:
            raise ValueError("a ReviewTask requires at least one Agent version")
        if created_at.tzinfo is None:
            raise ValueError("ReviewTask timestamps must be timezone-aware")
        return cls(
            task_id=task_id,
            repository_id=repository_id,
            repository_realpath_hash=repository_realpath_hash,
            git_common_dir_hash=git_common_dir_hash,
            scope=scope,
            target=target,
            selected_agent_versions=selected_agent_versions,
            created_at=created_at,
            overlay_artifact_ref=overlay_artifact_ref,
        )

    @property
    def status(self) -> ReviewStatus:
        """Return the current state without exposing a public status setter."""

        return self._status

    def next_happy_path_status(self) -> ReviewStatus:
        """Return the next deterministic success-path state."""

        try:
            return _HAPPY_PATH[_HAPPY_PATH.index(self._status) + 1]
        except (ValueError, IndexError) as error:
            raise InvalidReviewStateError("task has no next happy-path state") from error

    def transition_to(self, status: ReviewStatus, *, occurred_at: datetime | None = None) -> None:
        """Apply one allowed forward transition or fail without changing state."""

        if status not in _ALLOWED_TRANSITIONS.get(self._status, set()):
            raise InvalidReviewStateError(f"cannot transition {self._status} to {status}")
        timestamp = occurred_at or datetime.now(UTC)
        if self._status is ReviewStatus.CREATED:
            self.started_at = timestamp
        self._status = status
        if status in _TERMINAL:
            self.finished_at = timestamp

    def request_cancellation(self) -> None:
        """Persist cancellation intent for any non-terminal task."""

        if self._status in _TERMINAL:
            raise InvalidReviewStateError("terminal task cannot request cancellation")
        self.cancellation_requested = True

    def cancel(self, *, occurred_at: datetime | None = None) -> None:
        """Move any non-terminal task to CANCELED after propagation begins."""

        if self._status in _TERMINAL:
            raise InvalidReviewStateError("terminal task cannot be canceled")
        self._status = ReviewStatus.CANCELED
        self.finished_at = occurred_at or datetime.now(UTC)

    def fail(self, *, occurred_at: datetime | None = None) -> None:
        """Move any non-terminal task to FAILED with an explicit terminal timestamp."""

        if self._status in _TERMINAL:
            raise InvalidReviewStateError("terminal task cannot fail again")
        self._status = ReviewStatus.FAILED
        self.finished_at = occurred_at or datetime.now(UTC)

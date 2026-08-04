import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from codelens.review.domain.review_strategy import (
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
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
    PLANNING = "planning"
    REVIEWING = "reviewing"
    RESOLVING = "resolving"
    VERIFYING = "verifying"
    VALIDATING = "validating"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


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
    ReviewStatus.SUPERSEDED,
}
_ALLOWED_TRANSITIONS = {
    ReviewStatus.CREATED: {ReviewStatus.PROVISIONING_WORKTREE},
    ReviewStatus.PROVISIONING_WORKTREE: {ReviewStatus.SNAPSHOTTING},
    ReviewStatus.SNAPSHOTTING: {ReviewStatus.PREPARING},
    ReviewStatus.PLANNING: {ReviewStatus.REVIEWING},
    ReviewStatus.PREPARING: {ReviewStatus.PLANNING, ReviewStatus.REVIEWING},
    ReviewStatus.REVIEWING: {
        ReviewStatus.VALIDATING,
        ReviewStatus.RESOLVING,
        ReviewStatus.COMPLETED,
        ReviewStatus.PARTIAL,
    },
    ReviewStatus.RESOLVING: {
        ReviewStatus.VERIFYING,
        ReviewStatus.COMPLETED,
        ReviewStatus.PARTIAL,
    },
    ReviewStatus.VERIFYING: {ReviewStatus.COMPLETED, ReviewStatus.PARTIAL},
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
    repository_path: Path
    target_paths: tuple[str, ...]
    selected_agent_versions: tuple[str, ...]
    review_profile: ReviewProfileSnapshot
    planning_context_json: str
    planning_context_hash: str
    prompt_locale: str
    created_at: datetime
    trigger_source: Literal["manual", "plugin"] = "manual"
    supersede_policy: Literal["latest_snapshot", "preserve_all"] | None = None
    idempotency_key: str | None = None
    trigger_slot_key: str | None = None
    overlay_artifact_ref: str | None = None
    external_context: dict | None = None
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
        repository_path: Path,
        target_paths: tuple[str, ...],
        selected_agent_versions: tuple[str, ...],
        review_profile: ReviewProfileSnapshot | None = None,
        planning_context: Mapping[str, object] | None = None,
        trigger_source: Literal["manual", "plugin"] = "manual",
        supersede_policy: Literal["latest_snapshot", "preserve_all"] | None = None,
        idempotency_key: str | None = None,
        trigger_slot_key: str | None = None,
        created_at: datetime,
        overlay_artifact_ref: str | None = None,
        prompt_locale: str = "en",
        external_context: dict | None = None,
    ) -> "ReviewTask":
        """Create a task with a frozen strategy and an unplanned Adaptive actual team."""

        if review_profile is None:
            review_profile = ReviewProfileSnapshot(
                FixedReviewerSelection(selected_agent_versions)
            )
        initial_agents = cls._initial_agents(review_profile)
        if selected_agent_versions != initial_agents:
            selected_agent_versions = initial_agents
        if created_at.tzinfo is None:
            raise ValueError("ReviewTask timestamps must be timezone-aware")
        if not target_paths:
            raise ValueError("a ReviewTask requires at least one frozen target path")
        context = planning_context or {
            "schema_version": 1,
            "catalog_snapshot": {"version": "legacy", "reviewers": list(initial_agents)},
            "capability_readiness": {},
            "planner_execution_spec": None,
            "eligible_reviewer_execution_specs": [],
            "artifact_ids": [],
        }
        context_json = json.dumps(
            context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return cls(
            task_id=task_id,
            repository_id=repository_id,
            repository_realpath_hash=repository_realpath_hash,
            git_common_dir_hash=git_common_dir_hash,
            scope=scope,
            target=target,
            repository_path=repository_path.expanduser().resolve(),
            target_paths=target_paths,
            selected_agent_versions=selected_agent_versions,
            review_profile=review_profile,
            planning_context_json=context_json,
            planning_context_hash=hashlib.sha256(context_json.encode()).hexdigest(),
            trigger_source=trigger_source,
            supersede_policy=supersede_policy,
            idempotency_key=idempotency_key,
            trigger_slot_key=trigger_slot_key,
            prompt_locale=prompt_locale,
            created_at=created_at,
            overlay_artifact_ref=overlay_artifact_ref,
            external_context=external_context,
        )

    @staticmethod
    def _initial_agents(profile: ReviewProfileSnapshot) -> tuple[str, ...]:
        selection = profile.reviewer_selection
        return selection.reviewer_versions if isinstance(selection, FixedReviewerSelection) else ()

    def initial_selected_agent_versions(self) -> tuple[str, ...]:
        """Project a fixed request to its initial actual team; Adaptive remains unplanned."""

        return self._initial_agents(self.review_profile)

    def verify_planning_context(self) -> None:
        """Reject corrupted frozen planning input instead of resolving current configuration."""

        actual = hashlib.sha256(self.planning_context_json.encode()).hexdigest()
        if actual != self.planning_context_hash:
            raise ValueError("frozen planning context hash mismatch")

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

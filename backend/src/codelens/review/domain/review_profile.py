import re
from dataclasses import dataclass, replace
from datetime import datetime

from codelens.review.domain.review_strategy import (
    BudgetProfile,
    ReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.shared.domain.errors import DomainError

_PROFILE_ID_PATTERN = re.compile(r"profile-[a-z0-9][a-z0-9-]{0,119}\Z")


class ReviewProfileRevisionConflictError(DomainError, ValueError):
    """Reject an update whose optimistic revision no longer matches storage."""

    code = "review_profile_revision_conflict"


class ReviewProfileNotFoundError(DomainError, ValueError):
    """Report a missing profile without exposing persistence details."""

    code = "review_profile_not_found"


class ReviewProfileDefaultRequiredError(DomainError, ValueError):
    """Prevent mutations that would leave Review creation without a default."""

    code = "review_profile_default_required"


def _require_aware(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Review profile timestamps must be timezone-aware")


@dataclass(frozen=True)
class ReviewProfile:
    """Own one mutable named Review strategy under optimistic concurrency."""

    profile_id: str
    revision: int
    name: str
    is_default: bool
    reviewer_selection: ReviewerSelection
    budget_profile: BudgetProfile
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if _PROFILE_ID_PATTERN.fullmatch(self.profile_id) is None:
            raise ValueError("Review profile ID is invalid")
        if self.revision < 1:
            raise ValueError("Review profile revision must be positive")
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("Review profile name must contain 1 to 120 characters")
        _require_aware(self.created_at)
        _require_aware(self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("Review profile update cannot predate creation")

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        name: str,
        is_default: bool,
        reviewer_selection: ReviewerSelection,
        budget_profile: BudgetProfile,
        created_at: datetime,
    ) -> "ReviewProfile":
        """Create revision one while preserving an immutable identity and creation time."""

        return cls(
            profile_id=profile_id,
            revision=1,
            name=name.strip(),
            is_default=is_default,
            reviewer_selection=reviewer_selection,
            budget_profile=budget_profile,
            created_at=created_at,
            updated_at=created_at,
        )

    def update(
        self,
        *,
        expected_revision: int,
        name: str,
        is_default: bool,
        reviewer_selection: ReviewerSelection,
        budget_profile: BudgetProfile,
        updated_at: datetime,
    ) -> "ReviewProfile":
        """Return the next revision or fail before applying a stale write."""

        if expected_revision != self.revision:
            raise ReviewProfileRevisionConflictError("review profile revision conflict")
        return replace(
            self,
            revision=self.revision + 1,
            name=name.strip(),
            is_default=is_default,
            reviewer_selection=reviewer_selection,
            budget_profile=budget_profile,
            updated_at=updated_at,
        )

    def snapshot(self) -> ReviewProfileSnapshot:
        """Freeze strategy values and their source revision for a future Review."""

        return ReviewProfileSnapshot(
            reviewer_selection=self.reviewer_selection,
            budget_profile=self.budget_profile,
            source_profile_id=self.profile_id,
            source_profile_revision=self.revision,
        )

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from codelens.review.domain.ports import ReviewProfileRepository
from codelens.review.domain.review_profile import ReviewProfile
from codelens.review.domain.review_strategy import BudgetProfile, ReviewerSelection


def _new_profile_id() -> str:
    return f"profile-{uuid.uuid4().hex}"


class CreateReviewProfileHandler:
    """Create a profile and atomically preserve the singleton default invariant."""

    def __init__(
        self,
        repository: ReviewProfileRepository,
        *,
        id_factory: Callable[[], str] = _new_profile_id,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory
        self._clock = clock

    async def handle(
        self,
        *,
        name: str,
        is_default: bool,
        reviewer_selection: ReviewerSelection,
        budget_profile: BudgetProfile,
    ) -> ReviewProfile:
        profile = ReviewProfile.create(
            profile_id=self._id_factory(),
            name=name,
            is_default=is_default,
            reviewer_selection=reviewer_selection,
            budget_profile=budget_profile,
            created_at=self._clock(),
        )
        return await self._repository.create_review_profile(profile)


class UpdateReviewProfileHandler:
    """Apply a full profile replacement guarded by its current revision."""

    def __init__(
        self,
        repository: ReviewProfileRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def handle(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        name: str,
        is_default: bool,
        reviewer_selection: ReviewerSelection,
        budget_profile: BudgetProfile,
    ) -> ReviewProfile:
        return await self._repository.update_review_profile(
            profile_id,
            expected_revision=expected_revision,
            name=name,
            is_default=is_default,
            reviewer_selection=reviewer_selection,
            budget_profile=budget_profile,
            updated_at=self._clock(),
        )


class CopyReviewProfileHandler:
    """Copy strategy values into a new non-default revision-one profile."""

    def __init__(
        self,
        repository: ReviewProfileRepository,
        *,
        id_factory: Callable[[], str] = _new_profile_id,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory
        self._clock = clock

    async def handle(self, profile_id: str, *, name: str) -> ReviewProfile:
        return await self._repository.copy_review_profile(
            profile_id,
            new_profile_id=self._id_factory(),
            name=name,
            created_at=self._clock(),
        )


class DeleteReviewProfileHandler:
    """Delete only a non-default profile inside one invariant-checking transaction."""

    def __init__(self, repository: ReviewProfileRepository) -> None:
        self._repository = repository

    async def handle(self, profile_id: str) -> None:
        await self._repository.delete_review_profile(profile_id)


class SetDefaultReviewProfileHandler:
    """Replace the current default atomically under optimistic concurrency."""

    def __init__(
        self,
        repository: ReviewProfileRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def handle(
        self, profile_id: str, *, expected_revision: int
    ) -> ReviewProfile:
        return await self._repository.set_default_review_profile(
            profile_id,
            expected_revision=expected_revision,
            updated_at=self._clock(),
        )


class ListReviewProfilesHandler:
    """List profiles with the default first and stable identity ordering."""

    def __init__(self, repository: ReviewProfileRepository) -> None:
        self._repository = repository

    async def handle(self) -> tuple[ReviewProfile, ...]:
        return await self._repository.list_review_profiles()

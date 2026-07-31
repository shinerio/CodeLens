from datetime import UTC, datetime

import pytest

from codelens.review.domain.review_profile import ReviewProfile
from codelens.review.domain.review_strategy import AdaptiveReviewerSelection, BudgetProfile


def _balanced_profile() -> ReviewProfile:
    return ReviewProfile.create(
        profile_id="profile-balanced",
        name="Balanced Review",
        is_default=True,
        reviewer_selection=AdaptiveReviewerSelection(),
        budget_profile=BudgetProfile.STANDARD,
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
    )


def test_profile_update_increments_revision_without_changing_identity() -> None:
    profile = _balanced_profile()

    updated = profile.update(
        expected_revision=1,
        name="Balanced Deep Review",
        is_default=True,
        reviewer_selection=AdaptiveReviewerSelection(),
        budget_profile=BudgetProfile.DEEP,
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert updated.profile_id == profile.profile_id
    assert updated.revision == 2
    assert updated.budget_profile is BudgetProfile.DEEP
    assert updated.snapshot().source_profile_revision == 2


def test_stale_profile_update_is_rejected() -> None:
    with pytest.raises(ValueError, match="revision conflict"):
        _balanced_profile().update(
            expected_revision=4,
            name="stale",
            is_default=False,
            reviewer_selection=AdaptiveReviewerSelection(),
            budget_profile=BudgetProfile.STANDARD,
            updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_profile_requires_aware_timestamps_and_valid_identity() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ReviewProfile.create(
            profile_id="profile-invalid",
            name="Invalid",
            is_default=False,
            reviewer_selection=AdaptiveReviewerSelection(),
            budget_profile=BudgetProfile.STANDARD,
            created_at=datetime(2026, 7, 31),
        )

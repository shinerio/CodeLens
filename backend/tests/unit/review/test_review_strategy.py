import pytest

from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    BudgetProfile,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)


def test_fixed_rejects_general_with_specialists() -> None:
    with pytest.raises(ValueError, match="General reviewer must run alone"):
        FixedReviewerSelection(("general:v1", "security:v1"))


def test_fixed_rejects_legacy_correctness_team() -> None:
    with pytest.raises(ValueError, match="correctness:v1 is legacy single-reviewer only"):
        FixedReviewerSelection(("correctness:v1", "test-regression:v1"))


def test_profile_source_identity_is_all_or_nothing() -> None:
    with pytest.raises(ValueError, match="source profile identity is incomplete"):
        ReviewProfileSnapshot(
            reviewer_selection=AdaptiveReviewerSelection(),
            budget_profile=BudgetProfile.STANDARD,
            source_profile_id="profile-balanced",
            source_profile_revision=None,
        )


@pytest.mark.parametrize(
    "reviewer_versions, message",
    [
        ((), "at least one reviewer"),
        (("security:v1", "security:v1"), "duplicate reviewers"),
        (("Security:v1",), "invalid reference"),
        (("security:1",), "invalid reference"),
    ],
)
def test_fixed_rejects_invalid_reviewer_sets(
    reviewer_versions: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FixedReviewerSelection(reviewer_versions)


def test_fixed_accepts_general_legacy_and_specialist_selections() -> None:
    assert FixedReviewerSelection(("general:v1",)).mode == "fixed"
    assert FixedReviewerSelection(("correctness:v1",)).reviewer_versions == (
        "correctness:v1",
    )
    assert FixedReviewerSelection(
        ("correctness:v2", "security:v1")
    ).reviewer_versions == ("correctness:v2", "security:v1")


def test_budget_profile_exposes_only_the_approved_protocol_values() -> None:
    assert tuple(BudgetProfile) == (
        BudgetProfile.LEAN,
        BudgetProfile.STANDARD,
        BudgetProfile.DEEP,
    )


def test_profile_rejects_a_non_positive_source_revision() -> None:
    with pytest.raises(ValueError, match="source profile revision must be positive"):
        ReviewProfileSnapshot(
            reviewer_selection=AdaptiveReviewerSelection(),
            budget_profile=BudgetProfile.LEAN,
            source_profile_id="profile-balanced",
            source_profile_revision=0,
        )


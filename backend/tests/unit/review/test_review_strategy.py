import pytest

from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)


def test_fixed_rejects_general_with_specialists() -> None:
    with pytest.raises(ValueError, match="General reviewer must run alone"):
        FixedReviewerSelection(("general:v2", "security:v2"))


def test_profile_source_identity_is_all_or_nothing() -> None:
    with pytest.raises(ValueError, match="source profile identity is incomplete"):
        ReviewProfileSnapshot(
            reviewer_selection=AdaptiveReviewerSelection(),
            source_profile_id="profile-balanced",
            source_profile_revision=None,
        )


@pytest.mark.parametrize(
    "reviewer_versions, message",
    [
        ((), "at least one reviewer"),
        (("security:v2", "security:v2"), "duplicate reviewers"),
        (("Security:v1",), "invalid reference"),
        (("security:v1",), "invalid reference"),
        (("security:1",), "invalid reference"),
    ],
)
def test_fixed_rejects_invalid_reviewer_sets(
    reviewer_versions: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FixedReviewerSelection(reviewer_versions)


def test_fixed_accepts_general_and_specialist_selections() -> None:
    assert FixedReviewerSelection(("general:v2",)).mode == "fixed"
    assert FixedReviewerSelection(("correctness:v2",)).reviewer_versions == ("correctness:v2",)
    assert FixedReviewerSelection(("correctness:v2", "security:v2")).reviewer_versions == (
        "correctness:v2",
        "security:v2",
    )


def test_profile_rejects_a_non_positive_source_revision() -> None:
    with pytest.raises(ValueError, match="source profile revision must be positive"):
        ReviewProfileSnapshot(
            reviewer_selection=AdaptiveReviewerSelection(),
            source_profile_id="profile-balanced",
            source_profile_revision=0,
        )

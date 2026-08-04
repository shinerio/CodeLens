from datetime import UTC, datetime
from pathlib import Path

import pytest

from codelens.review.domain.models import (
    InvalidReviewStateError,
    ReviewStatus,
    ReviewTask,
)
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.workspace.domain.models import BranchScope, ReviewTarget


def _review_task(profile: ReviewProfileSnapshot | None = None) -> ReviewTask:
    return ReviewTask.create(
        task_id="review-1",
        repository_id="repository-1",
        repository_realpath_hash="c" * 64,
        git_common_dir_hash="d" * 64,
        repository_path=Path("/tmp/repository-1"),
        target_paths=("src/state.py",),
        scope=BranchScope(base_ref="main", target_ref="feature"),
        target=ReviewTarget("a" * 40, "b" * 40, None),
        selected_agent_versions=("correctness:v1",),
        review_profile=profile,
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def test_review_task_enforces_forward_worktree_first_state_sequence() -> None:
    task = _review_task()

    for status in (
        ReviewStatus.PROVISIONING_WORKTREE,
        ReviewStatus.SNAPSHOTTING,
        ReviewStatus.PREPARING,
        ReviewStatus.REVIEWING,
        ReviewStatus.VALIDATING,
        ReviewStatus.SYNTHESIZING,
        ReviewStatus.COMPLETED,
    ):
        task.transition_to(status)

    assert task.status is ReviewStatus.COMPLETED
    with pytest.raises(InvalidReviewStateError):
        task.transition_to(ReviewStatus.REVIEWING)


@pytest.mark.parametrize(
    "status",
    [
        ReviewStatus.CREATED,
        ReviewStatus.PROVISIONING_WORKTREE,
        ReviewStatus.SNAPSHOTTING,
        ReviewStatus.PREPARING,
        ReviewStatus.REVIEWING,
        ReviewStatus.VALIDATING,
        ReviewStatus.SYNTHESIZING,
    ],
)
def test_cancellation_is_valid_from_every_non_terminal_state(status: ReviewStatus) -> None:
    task = _review_task()
    while task.status is not status:
        task.transition_to(task.next_happy_path_status())

    task.request_cancellation()
    task.cancel()

    assert task.cancellation_requested
    assert task.status is ReviewStatus.CANCELED


def test_fixed_selection_preserves_order_as_actual_team() -> None:
    task = _review_task(
        ReviewProfileSnapshot(
            FixedReviewerSelection(("security:v1", "performance:v1")),
            source_profile_id="profile-team",
            source_profile_revision=2,
        )
    )

    assert task.selected_agent_versions == ("security:v1", "performance:v1")


def test_adaptive_selection_has_no_actual_team_before_planning() -> None:
    task = _review_task(
        ReviewProfileSnapshot(
            AdaptiveReviewerSelection(),
            source_profile_id="profile-auto",
            source_profile_revision=3,
        )
    )

    assert task.initial_selected_agent_versions() == ()

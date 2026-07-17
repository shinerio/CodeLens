from datetime import UTC, datetime
from pathlib import Path

import pytest

from codelens.review.domain.models import (
    InvalidReviewStateError,
    ReviewStatus,
    ReviewTask,
)
from codelens.workspace.domain.models import BranchScope, ReviewTarget


def _review_task() -> ReviewTask:
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

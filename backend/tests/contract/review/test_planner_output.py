import pytest
from pydantic import ValidationError

from codelens.review.infrastructure.planner_output import (
    PlannerOutputCodec,
    PlannerSelectionDto,
)
from codelens.review.infrastructure.planning_tools import ReviewPlanSubmissionCollector


def _payload() -> dict[str, object]:
    return {
        "schema_version": "1",
        "reviewer_references": ["security:v1", "performance:v1"],
    }


def _dto() -> PlannerSelectionDto:
    return PlannerSelectionDto.model_validate(_payload())


def test_planner_output_requires_exact_eligible_set() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=(),
    )

    selection = codec.decode(_payload())

    assert selection.reviewer_references == ("security:v1", "performance:v1")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value["reviewer_references"].pop(),
    ],
)
def test_planner_output_rejects_extra_missing_or_untrusted_values(mutate: object) -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=(),
    )
    payload = _payload()
    mutate(payload)  # type: ignore[operator]

    with pytest.raises((ValueError, ValidationError)):
        codec.decode(payload)


def test_planner_output_rejects_selecting_unavailable_reviewer() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=("security:v1",),
    )

    with pytest.raises(ValueError, match="unavailable"):
        codec.decode(_payload())


async def test_planner_submission_accumulates_batches_and_finalizes() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=(),
    )
    collector = ReviewPlanSubmissionCollector(codec)

    # Submit first batch
    submission = _dto()
    result = await collector.submit(submission)
    assert "Accepted 2 new Reviewer(s)" in result

    # Finalize the plan
    finalize_result = await collector.finalize()
    assert finalize_result == "Review Plan finalized."
    selection = collector.selection
    assert set(selection.reviewer_references) == {"security:v1", "performance:v1"}

    # Cannot submit after finalize
    with pytest.raises(ValueError, match="already finalized"):
        await collector.submit(submission)

    # Cannot finalize again
    with pytest.raises(ValueError, match="already finalized"):
        await collector.finalize()

    assert collector.selection is selection

import json

import pytest
from pydantic import ValidationError

from codelens.review.infrastructure.planner_output import (
    PlannerOutputCodec,
    PlannerSelectionDto,
)
from codelens.review.infrastructure.planning_tools import ReviewPlanSubmissionCollector


def _payload() -> dict[str, object]:
    return {
        "schema_version": "2",
        "reviewer_references": ["security:v2", "performance:v2"],
    }


def _dto() -> PlannerSelectionDto:
    return PlannerSelectionDto.model_validate(_payload())


def test_planner_output_accepts_a_specialist_subset() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=(
            "security:v2",
            "performance:v2",
            "correctness:v2",
            "general:v2",
        ),
        unavailable_reviewer_references=(),
    )

    selection = codec.decode(_payload())

    assert selection.reviewer_references == ("security:v2", "performance:v2")


def test_planner_output_accepts_general_alone() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v2", "performance:v2", "general:v2"),
        unavailable_reviewer_references=(),
    )

    selection = codec.decode({"schema_version": "2", "reviewer_references": ["general:v2"]})

    assert selection.reviewer_references == ("general:v2",)


@pytest.mark.parametrize(
    "reviewer_references",
    [
        ["security:v2"],
        ["general:v2", "security:v2"],
        ["security:v2", "security:v2"],
        ["security:v2", "unknown:v2"],
    ],
)
def test_planner_output_rejects_illegal_team_shapes(
    reviewer_references: list[str],
) -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v2", "performance:v2", "general:v2"),
        unavailable_reviewer_references=(),
    )

    with pytest.raises((ValueError, ValidationError)):
        codec.decode({"schema_version": "2", "reviewer_references": reviewer_references})


def test_planner_output_rejects_extra_fields() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v2", "performance:v2", "general:v2"),
        unavailable_reviewer_references=(),
    )
    payload = _payload()
    payload["unknown"] = True

    with pytest.raises(ValidationError):
        codec.decode(payload)


def test_planner_output_rejects_selecting_unavailable_reviewer() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v2", "performance:v2"),
        unavailable_reviewer_references=("security:v2",),
    )

    with pytest.raises(ValueError, match="unavailable"):
        codec.decode(_payload())


async def test_planner_submission_finalizes_once() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=(
            "security:v2",
            "performance:v2",
            "correctness:v2",
            "general:v2",
        ),
        unavailable_reviewer_references=(),
    )
    collector = ReviewPlanSubmissionCollector(codec)

    finalize_result = await collector.finalize(_dto())
    assert json.loads(finalize_result)["data"]["reviewer_count"] == 2
    selection = collector.selection
    assert set(selection.reviewer_references) == {"security:v2", "performance:v2"}

    repeated = json.loads(await collector.finalize(_dto()))
    assert repeated["status"] == "rejected"
    assert repeated["diagnostics"][0]["code"] == "plan_already_finalized"

    assert collector.selection is selection

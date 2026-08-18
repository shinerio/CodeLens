import json

import pytest

from codelens.review.infrastructure.planner_output import PlannerOutputCodec
from codelens.review.infrastructure.planning_tools import ReviewPlanSubmissionCollector


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

    selection = codec.decode_references(["security:v2", "performance:v2"])

    assert selection.reviewer_references == ("security:v2", "performance:v2")
    assert selection.schema_version == "2"


def test_planner_output_accepts_general_alone() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v2", "performance:v2", "general:v2"),
        unavailable_reviewer_references=(),
    )

    selection = codec.decode_references(["general:v2"])

    assert selection.reviewer_references == ("general:v2",)


def test_planner_output_accepts_single_specialist() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v2", "performance:v2", "general:v2"),
        unavailable_reviewer_references=(),
    )

    selection = codec.decode_references(["security:v2"])

    assert selection.reviewer_references == ("security:v2",)


@pytest.mark.parametrize(
    "reviewer_references",
    [
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

    with pytest.raises(ValueError):
        codec.decode_references(reviewer_references)


def test_planner_output_rejects_selecting_unavailable_reviewer() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v2", "performance:v2"),
        unavailable_reviewer_references=("security:v2",),
    )

    with pytest.raises(ValueError, match="unavailable"):
        codec.decode_references(["security:v2", "performance:v2"])


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

    finalize_result = await collector.finalize(["security:v2", "performance:v2"])
    assert json.loads(finalize_result)["data"]["reviewer_count"] == 2
    selection = collector.selection
    assert set(selection.reviewer_references) == {"security:v2", "performance:v2"}

    repeated = json.loads(await collector.finalize(["security:v2", "performance:v2"]))
    assert repeated["status"] == "rejected"
    assert repeated["diagnostics"][0]["code"] == "plan_already_finalized"

    assert collector.selection is selection

import json

from codelens.review.infrastructure.planner_output import PlannerOutputCodec
from codelens.review.infrastructure.planning_tools import ReviewPlanSubmissionCollector


def _collector() -> ReviewPlanSubmissionCollector:
    return ReviewPlanSubmissionCollector(
        PlannerOutputCodec(
            eligible_reviewer_references=("security:v2", "performance:v2", "general:v2"),
            unavailable_reviewer_references=(),
        )
    )


async def test_finalize_plan_returns_success_envelope_and_rejects_repeat() -> None:
    collector = _collector()

    accepted = json.loads(await collector.finalize(["security:v2", "performance:v2"]))
    repeated = json.loads(await collector.finalize(["security:v2", "performance:v2"]))

    assert accepted["status"] == "success"
    assert accepted["data"]["reviewer_count"] == 2
    assert repeated["status"] == "rejected"
    assert repeated["diagnostics"][0]["code"] == "plan_already_finalized"


async def test_finalize_plan_rejects_invalid_business_selection() -> None:
    collector = _collector()

    result = json.loads(await collector.finalize([]))

    assert result["status"] == "rejected"
    assert result["diagnostics"][0]["code"] == "invalid_reviewer_selection"
    assert collector.is_completed is False

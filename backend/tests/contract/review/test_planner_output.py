import pytest
from pydantic import ValidationError

from codelens.review.infrastructure.planner_output import PlannerOutputCodec
from codelens.review.infrastructure.planning_tools import ReviewPlanSubmissionCollector


def _payload() -> dict[str, object]:
    return {
        "schema_version": "1",
        "strategy": "specialist_team",
        "risk_signals": [
            {"code": "auth-boundary", "evidence_paths": ["src/auth.py"]}
        ],
        "reviewer_decisions": [
            {
                "reviewer_reference": "security:v1",
                "is_selected": True,
                "reason_codes": ["security-risk"],
                "focus_paths": ["src/auth.py"],
            },
            {
                "reviewer_reference": "performance:v1",
                "is_selected": False,
                "reason_codes": ["not-indicated"],
                "focus_paths": [],
            },
        ],
    }


def test_planner_output_requires_exact_eligible_set_and_snapshot_paths() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=(),
        target_paths=("src/auth.py",),
        allowed_reason_codes=frozenset(
            {"auth-boundary", "security-risk", "not-indicated", "broad-risk"}
        ),
    )

    selection = codec.decode(_payload())

    assert selection.reviewer_references == ("security:v1",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value["reviewer_decisions"].pop(),
        lambda value: value["reviewer_decisions"][0].update(
            {"focus_paths": ["../secret"]}
        ),
        lambda value: value["reviewer_decisions"][0].update(
            {"reason_codes": ["free-form-reason"]}
        ),
    ],
)
def test_planner_output_rejects_extra_missing_or_untrusted_values(mutate: object) -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=(),
        target_paths=("src/auth.py",),
        allowed_reason_codes=frozenset(
            {"auth-boundary", "security-risk", "not-indicated"}
        ),
    )
    payload = _payload()
    mutate(payload)  # type: ignore[operator]

    with pytest.raises((ValueError, ValidationError)):
        codec.decode(payload)


def test_planner_output_rejects_selecting_unavailable_reviewer() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=("security:v1",),
        target_paths=("src/auth.py",),
        allowed_reason_codes=frozenset(
            {"auth-boundary", "security-risk", "not-indicated"}
        ),
    )

    with pytest.raises(ValueError, match="unavailable"):
        codec.decode(_payload())


async def test_planner_submission_accepts_exactly_one_validated_output() -> None:
    codec = PlannerOutputCodec(
        eligible_reviewer_references=("security:v1", "performance:v1"),
        unavailable_reviewer_references=(),
        target_paths=("src/auth.py",),
        allowed_reason_codes=frozenset(
            {"auth-boundary", "security-risk", "not-indicated"}
        ),
    )
    collector = ReviewPlanSubmissionCollector(codec)
    from codelens.review.infrastructure.planner_output import PlannerSelectionDto

    submission = PlannerSelectionDto.model_validate(_payload())
    assert await collector.submit(submission) == "Review Plan accepted."
    accepted = collector.selection

    with pytest.raises(ValueError, match="only once"):
        await collector.submit(submission)
    assert collector.selection is accepted

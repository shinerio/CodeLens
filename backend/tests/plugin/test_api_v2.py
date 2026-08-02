from datetime import UTC, datetime

import pytest

from codelens.plugin.api.v2 import (
    AdaptiveReviewerSelection,
    FindingExportEnvelopeV2,
    FixedReviewerSelection,
    ReportSinkPort,
    TriggerReviewPolicy,
)
from codelens.review.application.export_findings import (
    ReviewCoverageDto,
    ReviewExportMetaV2,
    ReviewPlanSummaryDto,
    SelectionRequestDto,
    to_v1_export_envelope,
)


def test_fixed_policy_preserves_ordered_exact_versions() -> None:
    policy = TriggerReviewPolicy.from_config(
        {
            "reviewer_selection": {
                "mode": "fixed",
                "reviewer_versions": ["security:v1", "correctness:v2"],
            },
            "budget_profile": "standard",
            "supersede_policy": "latest_snapshot",
            "prompt_locale": "en",
        }
    )

    assert isinstance(policy.reviewer_selection, FixedReviewerSelection)
    assert policy.reviewer_selection.reviewer_versions == (
        "security:v1",
        "correctness:v2",
    )


def test_adaptive_policy_rejects_fixed_reviewers() -> None:
    with pytest.raises(ValueError, match="adaptive"):
        TriggerReviewPolicy.from_config(
            {
                "reviewer_selection": {
                    "mode": "adaptive",
                    "reviewer_versions": ["security:v1"],
                },
                "budget_profile": "deep",
                "supersede_policy": "preserve_all",
                "prompt_locale": "en",
            }
        )


def test_adaptive_policy_is_typed() -> None:
    policy = TriggerReviewPolicy.from_config(
        {
            "reviewer_selection": {"mode": "adaptive"},
            "budget_profile": "deep",
            "supersede_policy": "preserve_all",
            "prompt_locale": "zh-CN",
        }
    )
    assert isinstance(policy.reviewer_selection, AdaptiveReviewerSelection)


def test_general_must_be_the_only_fixed_reviewer() -> None:
    with pytest.raises(ValueError, match="general:v1"):
        TriggerReviewPolicy.from_config(
            {
                "reviewer_selection": {
                    "mode": "fixed",
                    "reviewer_versions": ["general:v1", "security:v1"],
                },
                "budget_profile": "lean",
                "supersede_policy": "latest_snapshot",
                "prompt_locale": "en",
            }
        )


def test_report_contract_is_available_only_through_the_public_v2_surface() -> None:
    assert FindingExportEnvelopeV2.__name__ == "FindingExportEnvelopeV2"
    assert ReportSinkPort.__name__ == "ReportSinkPort"


def test_historical_v1_envelope_projection_remains_available() -> None:
    envelope = FindingExportEnvelopeV2(
        schema_version="2.0",
        exported_at=datetime(2026, 7, 31, tzinfo=UTC),
        review=ReviewExportMetaV2(
            task_id="review_fixture",
            repository_name="fixture",
            scope_type="commit",
            base_oid="a" * 40,
            head_oid="b" * 40,
            base_ref=None,
            target_ref="HEAD",
            status="completed",
            selection_request=SelectionRequestDto("fixed", ("correctness:v1",)),
            plan_summary=ReviewPlanSummaryDto(
                "fixed", ("correctness:v1",), None, "c" * 64
            ),
            coverage=ReviewCoverageDto(("correctness:v1",), (), ()),
            created_at=datetime(2026, 7, 31, tzinfo=UTC),
        ),
        findings=(),
    )

    historical = to_v1_export_envelope(envelope)

    assert historical.schema_version == "1.0"
    assert historical.review.selected_agent_versions == ("correctness:v1",)

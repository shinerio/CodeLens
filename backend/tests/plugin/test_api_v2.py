import pytest

from codelens.plugin.api.v2 import (
    AdaptiveReviewerSelection,
    ExistingFindingV2,
    FindingExportEnvelopeV2,
    FixedReviewerSelection,
    ReportSinkPort,
    TriggerReviewPolicy,
)


def test_fixed_policy_preserves_ordered_exact_versions() -> None:
    policy = TriggerReviewPolicy.from_config(
        {
            "reviewer_selection": {
                "mode": "fixed",
                "reviewer_versions": ["security:v2", "correctness:v2"],
            },
            "supersede_policy": "latest_snapshot",
            "prompt_locale": "en",
        }
    )

    assert isinstance(policy.reviewer_selection, FixedReviewerSelection)
    assert policy.reviewer_selection.reviewer_versions == (
        "security:v2",
        "correctness:v2",
    )


def test_adaptive_policy_rejects_fixed_reviewers() -> None:
    with pytest.raises(ValueError, match="adaptive"):
        TriggerReviewPolicy.from_config(
            {
                "reviewer_selection": {
                    "mode": "adaptive",
                    "reviewer_versions": ["security:v2"],
                },
                "supersede_policy": "preserve_all",
                "prompt_locale": "en",
            }
        )


def test_adaptive_policy_is_typed() -> None:
    policy = TriggerReviewPolicy.from_config(
        {
            "reviewer_selection": {"mode": "adaptive"},
            "supersede_policy": "preserve_all",
            "prompt_locale": "zh-CN",
        }
    )
    assert isinstance(policy.reviewer_selection, AdaptiveReviewerSelection)


def test_general_must_be_the_only_fixed_reviewer() -> None:
    with pytest.raises(ValueError, match="general:v2"):
        TriggerReviewPolicy.from_config(
            {
                "reviewer_selection": {
                    "mode": "fixed",
                    "reviewer_versions": ["general:v2", "security:v2"],
                },
                "supersede_policy": "latest_snapshot",
                "prompt_locale": "en",
            }
        )


def test_report_contract_is_available_only_through_the_public_v2_surface() -> None:
    assert FindingExportEnvelopeV2.__name__ == "FindingExportEnvelopeV2"
    assert ReportSinkPort.__name__ == "ReportSinkPort"


def test_trigger_plugins_can_submit_structured_existing_findings_through_v2() -> None:
    finding = ExistingFindingV2(
        source_id="github",
        finding_id="PRRC_kwDO-example",
        title="Existing PR comment",
        content="This issue was reported on the prior PR revision.",
        path="src/service.py",
        side="new",
        start_line=12,
        end_line=12,
        existing_code="return account.name",
    )

    assert finding.as_payload()["source_id"] == "github"

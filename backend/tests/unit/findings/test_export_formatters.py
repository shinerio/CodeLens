"""Tests for export formatters, especially zero-findings report rendering.

A zero-findings review is a valid outcome; the markdown report must state it
explicitly instead of ending bare after the metadata block.
"""

from datetime import UTC, datetime

from codelens.findings.infrastructure.export_formatters import (
    JsonFindingExportFormatter,
    MarkdownFindingExportFormatter,
)
from codelens.review.application.export_findings import (
    FindingExportEnvelope,
    ReviewCoverageDto,
    ReviewExportMeta,
    ReviewPlanSummaryDto,
    SelectionRequestDto,
)

_CREATED_AT = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)


def _envelope(findings: tuple[object, ...] = ()) -> FindingExportEnvelope:
    """Build a minimal envelope with the given findings (empty by default)."""

    return FindingExportEnvelope(
        schema_version="2.0",
        exported_at=_CREATED_AT,
        review=ReviewExportMeta(
            task_id="review_zero",
            repository_name="fixture",
            scope_type="commit",
            base_oid="a" * 40,
            head_oid="b" * 40,
            base_ref=None,
            target_ref=None,
            status="completed",
            selection_request=SelectionRequestDto(
                mode="fixed",
                reviewer_versions=("correctness:v2",),
            ),
            plan_summary=ReviewPlanSummaryDto(
                strategy="fixed",
                selected_reviewer_versions=("correctness:v2",),
                planner_version=None,
                plan_hash="a" * 64,
            ),
            coverage=ReviewCoverageDto(
                completed_reviewer_versions=("correctness:v2",),
                failed_reviewer_versions=(),
                omitted_reviewer_versions=(),
            ),
            created_at=_CREATED_AT,
        ),
        findings=findings,  # type: ignore[arg-type]
    )


def test_markdown_formatter_states_no_findings_explicitly() -> None:
    """Zero-findings markdown report includes an explicit no-findings conclusion."""

    content = MarkdownFindingExportFormatter().format(_envelope(()))
    text = content.decode("utf-8")

    assert "本次评审未发现意见。" in text
    assert "- **Findings:** 0" in text


def test_json_formatter_serializes_empty_findings() -> None:
    """Zero-findings JSON report serializes findings as an empty array."""

    content = JsonFindingExportFormatter().format(_envelope(()))
    text = content.decode("utf-8")

    assert '"findings": []' in text

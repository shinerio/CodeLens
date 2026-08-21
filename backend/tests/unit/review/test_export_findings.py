"""Tests for ExportFindingsHandler.build_envelope zero-findings behavior.

A completed review with no findings must still produce a valid envelope so
downstream plugins and reports (label application, local file export) run
instead of failing the export with "No findings to export".
"""

from datetime import UTC, datetime
from pathlib import Path

from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingDisposition,
    FindingSeverity,
    SourceLocation,
)
from codelens.findings.domain.remediation import RemediationDecision
from codelens.review.application.export_findings import ExportFindingsHandler
from codelens.review.domain.ports import ReviewExecutionRecord, ReviewRecord
from codelens.review.domain.review_strategy import (
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)

_TASK_ID = "review_" + "a" * 32
_CREATED_AT = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
_EXCLUSION_POLICY_JSON = '{"exclude_binary":true,"path_regexes":[],"suffixes":[]}'
_EXCLUSION_POLICY_HASH = (
    "f135f14995e69bb776fd5c18af7fa0d19e45f867501b3274e9cb38cfbc7676c3"
)


def _review(*, status: str = "completed") -> ReviewRecord:
    """Build a minimal valid ReviewRecord for export tests."""

    return ReviewRecord(
        task_id=_TASK_ID,
        repository_id="repo-1",
        repository_realpath_hash="a" * 64,
        git_common_dir_hash="b" * 64,
        scope_type="branch",
        base_oid="c" * 40,
        head_oid="d" * 40,
        base_ref="main",
        target_ref="feature",
        selected_agent_versions=("correctness:v2",),
        review_profile=ReviewProfileSnapshot(FixedReviewerSelection(("correctness:v2",))),
        planning_context_json=None,
        planning_context_hash=None,
        trigger_source="webhook",
        supersede_policy=None,
        status=status,
        cancellation_requested=False,
        repository_name="fixture",
        created_at=_CREATED_AT,
        is_deleted=False,
    )


def _execution() -> ReviewExecutionRecord:
    """Build a minimal valid ReviewExecutionRecord for export tests."""

    return ReviewExecutionRecord(
        task_id=_TASK_ID,
        repository_path=Path("/repo"),
        repository_realpath_hash="a" * 64,
        git_common_dir_hash="b" * 64,
        base_oid="c" * 40,
        head_oid="d" * 40,
        scope_type="branch",
        base_ref="main",
        target_ref="feature",
        overlay_hash=None,
        overlay_artifact_ref=None,
        candidate_paths=("src/example.py",),
        file_exclusion_policy_json=_EXCLUSION_POLICY_JSON,
        file_exclusion_policy_hash=_EXCLUSION_POLICY_HASH,
        selected_agent_versions=("correctness:v2",),
        prompt_locale="en",
        status="completed",
        cancellation_requested=False,
    )


def _finding() -> Finding:
    """Build one finding referencing src/example.py lines 3-4."""

    return Finding(
        finding_id="finding-1",
        fingerprint="f" * 64,
        reviewer_id="correctness",
        category="correctness",
        title="Example",
        severity=FindingSeverity.HIGH,
        disposition=FindingDisposition.BLOCKING,
        confidence=0.9,
        primary_location=SourceLocation(
            "src/example.py",
            3,
            4,
            "new",
            "e" * 64,
            False,
        ),
        related_locations=(),
        changed_hunk_id="hunk-1",
        change_origin=ChangeOrigin.INTRODUCED,
        evidence=(Evidence("excerpt", "proof", None, "e" * 64),),
        impact="impact",
        explanation="explanation",
        reproduction=None,
        recommendation="recommendation",
        rule_sources=(),
    )


class _FakeStore:
    """In-memory review store for build_envelope tests."""

    def __init__(self, *, findings: tuple[Finding, ...]) -> None:
        self._findings = findings

    async def get_review(self, _task_id: str) -> ReviewRecord:
        return _review()

    async def get_execution(self, _task_id: str) -> ReviewExecutionRecord:
        return _execution()

    async def list_findings(self, _task_id: str) -> tuple[Finding, ...]:
        return self._findings

    async def list_remediation_decisions(self, _task_id: str) -> tuple[RemediationDecision, ...]:
        return ()


class _FakeReader:
    """Revision reader returning canned source bytes for base/head OIDs."""

    async def read_revision_optional(
        self,
        _repository: Path,
        revision: str,
        _path: str,
    ) -> bytes | None:
        if revision == "c" * 40:
            return b"one\ntwo\nold three\nfour\nfive\n"
        if revision == "d" * 40:
            return b"one\ntwo\nthree\nfour\nfive\n"
        return None


async def test_build_envelope_returns_empty_findings_for_zero_finding_review() -> None:
    """A completed review with zero findings yields a valid empty envelope."""

    handler = ExportFindingsHandler(_FakeStore(findings=()), _FakeReader())

    envelope = await handler.build_envelope(_TASK_ID)

    assert envelope.findings == ()
    assert envelope.review.task_id == _TASK_ID
    assert envelope.review.status == "completed"


async def test_build_envelope_includes_findings_when_present() -> None:
    """Non-zero findings are still exported (regression guard)."""

    handler = ExportFindingsHandler(_FakeStore(findings=(_finding(),)), _FakeReader())

    envelope = await handler.build_envelope(_TASK_ID)

    assert len(envelope.findings) == 1
    assert envelope.findings[0].finding_id == "finding-1"

"""Export review findings with source code snippets for AI agents and human review."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from zoneinfo import ZoneInfo

from codelens.findings.domain.models import Evidence, Finding, RuleReference, SourceLocation
from codelens.review.domain.ports import ReviewExecutionRecord, ReviewPlanRecord, ReviewRecord
from codelens.review.domain.review_plan import ReviewPlanNodeType
from codelens.review.domain.review_strategy import AdaptiveReviewerSelection


class _ReviewStorePort(Protocol):
    async def get_review(self, task_id: str) -> ReviewRecord | None: ...

    async def get_execution(self, task_id: str) -> ReviewExecutionRecord | None: ...

    async def list_findings(self, task_id: str) -> Sequence[Finding]: ...


class _RevisionReaderPort(Protocol):
    async def read_revision_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
    ) -> bytes | None: ...


class _ReviewPlanStorePort(Protocol):
    async def get(self, task_id: str) -> ReviewPlanRecord | None: ...


class _CheckpointView(Protocol):
    status: str
    agent_version: str | None


class _CheckpointStorePort(Protocol):
    async def list_for_task(self, task_id: str) -> Sequence[_CheckpointView]: ...


@dataclass(frozen=True)
class SourceSnippetVersion:
    """One slice of source code from a fixed revision."""

    revision: str
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True)
class SourceSnippet:
    """Base and target source snippets for one finding location."""

    base: SourceSnippetVersion | None
    target: SourceSnippetVersion | None


@dataclass(frozen=True)
class FindingExportItem:
    """One finding with its source code context."""

    finding_id: str
    fingerprint: str
    reviewer_id: str
    category: str
    title: str
    severity: str
    disposition: str
    confidence: float | None
    change_origin: str
    changed_hunk_id: str | None
    primary_location: SourceLocation
    related_locations: tuple[SourceLocation, ...]
    evidence: tuple[Evidence, ...]
    impact: str
    explanation: str
    reproduction: str | None
    recommendation: str
    rule_sources: tuple[RuleReference, ...]
    source_excerpt: SourceSnippet


@dataclass(frozen=True)
class SelectionRequestDto:
    """Immutable reviewer selection requested before planning."""

    mode: Literal["fixed", "adaptive"]
    reviewer_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewPlanSummaryDto:
    """Stable summary of the persisted, hash-verified Review Plan."""

    strategy: Literal["fixed", "adaptive"]
    selected_reviewer_versions: tuple[str, ...]
    planner_version: str | None
    plan_hash: str


@dataclass(frozen=True)
class ReviewCoverageDto:
    """Terminal reviewer coverage without exposing orchestration checkpoints."""

    completed_reviewer_versions: tuple[str, ...]
    failed_reviewer_versions: tuple[str, ...]
    omitted_reviewer_versions: tuple[str, ...]


@dataclass(frozen=True)
class ReviewExportMetaV2:
    """Published review metadata for report envelope 2.0."""

    task_id: str
    repository_name: str
    scope_type: str
    base_oid: str
    head_oid: str
    base_ref: str | None
    target_ref: str | None
    status: Literal["completed", "partial"]
    selection_request: SelectionRequestDto
    plan_summary: ReviewPlanSummaryDto
    coverage: ReviewCoverageDto
    created_at: datetime
    external_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class FindingExportEnvelopeV2:
    """Canonical Published-Finding export structure for plugin API v2."""

    schema_version: Literal["2.0"]
    exported_at: datetime
    review: ReviewExportMetaV2
    findings: tuple[FindingExportItem, ...]


# Canonical aliases keep domain-facing names concise while the public plugin
# contract remains explicitly versioned.
ReviewExportMeta = ReviewExportMetaV2
FindingExportEnvelope = FindingExportEnvelopeV2


class FindingExportFormatterPort(Protocol):
    """Format an export envelope into bytes for a specific output format."""

    @property
    def format_id(self) -> str:
        """Return the stable format identifier (e.g. 'json', 'markdown')."""
        ...

    @property
    def media_type(self) -> str:
        """Return the HTTP media type for this format."""
        ...

    @property
    def file_extension(self) -> str:
        """Return the file extension (without dot)."""
        ...

    def format(self, envelope: FindingExportEnvelope) -> bytes:
        """Serialize the envelope into the target format."""
        ...


_EXPORTABLE_STATUSES = {"completed", "partial"}
_SNIPPET_CONTEXT_LINES = 3


class ExportFindingsHandler:
    """Orchestrate findings export with source code snippets."""

    def __init__(
        self,
        store: _ReviewStorePort,
        reader: _RevisionReaderPort,
        plan_store: _ReviewPlanStorePort | None = None,
        checkpoint_store: _CheckpointStorePort | None = None,
    ) -> None:
        self._store = store
        self._reader = reader
        self._plan_store = plan_store
        self._checkpoint_store = checkpoint_store

    async def handle(
        self, task_id: str, formatter: FindingExportFormatterPort
    ) -> tuple[bytes, str, str]:
        """Return (content_bytes, media_type, filename) for the export."""

        envelope = await self.build_envelope(task_id)
        content = formatter.format(envelope)
        short_task_id = task_id.replace("review_", "")[:8]
        filename = f"codelens-review-{short_task_id}-findings.{formatter.file_extension}"
        return content, formatter.media_type, filename

    async def build_envelope(self, task_id: str) -> FindingExportEnvelope:
        """Build and return the structured export envelope for one review.

        Validates that the review exists and is in a terminal state. A review
        with no findings produces a valid envelope with an empty findings
        tuple so downstream plugins and reports still run (label application,
        local file export, etc.). Reads source snippets from pinned revisions.
        """

        review = await self._store.get_review(task_id)
        if review is None:
            raise KeyError(task_id)
        if review.status not in _EXPORTABLE_STATUSES:
            raise ValueError(
                f"Review is not in a terminal state: {review.status}. "
                "Export is only available for completed reviews."
            )

        execution = await self._store.get_execution(task_id)
        if execution is None:
            raise KeyError(task_id)

        findings = await self._store.list_findings(task_id)

        return await self._build_envelope_from_findings(task_id, review, execution, findings)

    async def _build_envelope_from_findings(
        self,
        task_id: str,
        review: ReviewRecord,
        execution: ReviewExecutionRecord,
        findings: Sequence[Finding],
    ) -> FindingExportEnvelope:
        """Assemble the envelope from already-loaded review data."""

        export_items: list[FindingExportItem] = []
        for finding in findings:
            source_excerpt = await self._build_source_snippet(execution, finding.primary_location)

            export_items.append(
                FindingExportItem(
                    finding_id=finding.finding_id,
                    fingerprint=finding.fingerprint,
                    reviewer_id=finding.reviewer_id,
                    category=finding.category,
                    title=finding.title,
                    severity=finding.severity.value,
                    disposition=finding.disposition.value,
                    confidence=finding.confidence,
                    change_origin=finding.change_origin.value,
                    changed_hunk_id=finding.changed_hunk_id,
                    primary_location=finding.primary_location,
                    related_locations=finding.related_locations,
                    evidence=finding.evidence,
                    impact=finding.impact,
                    explanation=finding.explanation,
                    reproduction=finding.reproduction,
                    recommendation=finding.recommendation,
                    rule_sources=finding.rule_sources,
                    source_excerpt=source_excerpt,
                )
            )

        plan_record = await self._plan_store.get(task_id) if self._plan_store is not None else None
        selected_versions = (
            plan_record.plan.reviewer_references
            if plan_record is not None
            else review.selected_agent_versions
        )
        selection = review.review_profile.reviewer_selection
        selection_mode: Literal["fixed", "adaptive"] = (
            "adaptive" if isinstance(selection, AdaptiveReviewerSelection) else "fixed"
        )
        requested_versions = (
            () if isinstance(selection, AdaptiveReviewerSelection) else selection.reviewer_versions
        )
        planner_version = None
        if plan_record is not None:
            planner_version = next(
                (
                    node.agent_reference
                    for node in plan_record.plan.nodes
                    if node.node_type is ReviewPlanNodeType.PLANNER
                ),
                None,
            )
        checkpoints = (
            await self._checkpoint_store.list_for_task(task_id)
            if self._checkpoint_store is not None
            else ()
        )
        reviewer_status = {
            checkpoint.agent_version: checkpoint.status
            for checkpoint in checkpoints
            if checkpoint.agent_version in selected_versions
        }
        completed = tuple(
            version
            for version in selected_versions
            if reviewer_status.get(version, "succeeded" if review.status == "completed" else "")
            == "succeeded"
        )
        failed = tuple(
            version
            for version in selected_versions
            if reviewer_status.get(version) in {"failed", "timed_out", "canceled"}
        )
        omitted = tuple(
            version
            for version in selected_versions
            if version not in completed and version not in failed
        )
        fallback_plan_hash = hashlib.sha256(
            json.dumps(
                {"strategy": selection_mode, "reviewers": selected_versions},
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return FindingExportEnvelopeV2(
            schema_version="2.0",
            exported_at=datetime.now(ZoneInfo("UTC")),
            review=ReviewExportMetaV2(
                task_id=review.task_id,
                repository_name=review.repository_name,
                scope_type=review.scope_type,
                base_oid=review.base_oid,
                head_oid=review.head_oid,
                base_ref=review.base_ref,
                target_ref=review.target_ref,
                status=cast(Literal["completed", "partial"], review.status),
                selection_request=SelectionRequestDto(
                    mode=selection_mode,
                    reviewer_versions=requested_versions,
                ),
                plan_summary=ReviewPlanSummaryDto(
                    strategy=selection_mode,
                    selected_reviewer_versions=selected_versions,
                    planner_version=planner_version,
                    plan_hash=(
                        plan_record.plan.plan_hash
                        if plan_record is not None
                        else fallback_plan_hash
                    ),
                ),
                coverage=ReviewCoverageDto(
                    completed_reviewer_versions=completed,
                    failed_reviewer_versions=failed,
                    omitted_reviewer_versions=omitted,
                ),
                created_at=review.created_at,
                external_context=review.external_context,
            ),
            findings=tuple(export_items),
        )

    async def _build_source_snippet(
        self, execution: ReviewExecutionRecord, location: SourceLocation
    ) -> SourceSnippet:
        """Read and slice source code for one finding location."""

        base_source = await self._reader.read_revision_optional(
            execution.repository_path, execution.base_oid, location.path
        )
        target_source = await self._reader.read_revision_optional(
            execution.repository_path, execution.head_oid, location.path
        )

        base_snippet = (
            self._slice_content(
                base_source, location.start_line, location.end_line, execution.base_oid
            )
            if base_source is not None
            else None
        )
        target_snippet = (
            self._slice_content(
                target_source, location.start_line, location.end_line, execution.head_oid
            )
            if target_source is not None
            else None
        )

        return SourceSnippet(base=base_snippet, target=target_snippet)

    @staticmethod
    def _slice_content(
        source: bytes, start_line: int, end_line: int, revision: str
    ) -> SourceSnippetVersion:
        """Extract lines [start_line, end_line] with 3-line context."""

        text = source.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)

        slice_start = max(0, start_line - 1 - _SNIPPET_CONTEXT_LINES)
        slice_end = min(len(lines), end_line + _SNIPPET_CONTEXT_LINES)

        sliced_lines = lines[slice_start:slice_end]
        content = "".join(sliced_lines)

        return SourceSnippetVersion(
            revision=revision,
            start_line=slice_start + 1,
            end_line=slice_end,
            content=content,
        )

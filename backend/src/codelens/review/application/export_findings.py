"""Export review findings with source code snippets for AI agents and human review."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from codelens.findings.domain.models import Finding, RuleReference, SourceLocation
from codelens.review.domain.ports import ReviewExecutionRecord, ReviewRecord


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
    confidence: float
    change_origin: str
    changed_hunk_id: str | None
    primary_location: SourceLocation
    related_locations: tuple[SourceLocation, ...]
    evidence: tuple
    impact: str
    explanation: str
    reproduction: str | None
    recommendation: str
    rule_sources: tuple[RuleReference, ...]
    source_excerpt: SourceSnippet


@dataclass(frozen=True)
class ReviewExportMeta:
    """Review metadata for the export envelope."""

    task_id: str
    repository_name: str
    scope_type: str
    base_oid: str
    head_oid: str
    selected_agent_versions: tuple[str, ...]
    status: str
    created_at: datetime
    external_context: dict | None = None


@dataclass(frozen=True)
class FindingExportEnvelope:
    """Canonical export structure with versioned schema."""

    schema_version: str
    exported_at: datetime
    review: ReviewExportMeta
    findings: tuple[FindingExportItem, ...]


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


_TERMINAL_STATUSES = {"completed", "partial", "failed", "canceled"}
_SNIPPET_CONTEXT_LINES = 3


class ExportFindingsHandler:
    """Orchestrate findings export with source code snippets."""

    def __init__(self, store: _ReviewStorePort, reader: _RevisionReaderPort) -> None:
        self._store = store
        self._reader = reader

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

        Validates that the review exists, is in a terminal state, and has
        findings. Reads source snippets from pinned revisions.
        """

        review = await self._store.get_review(task_id)
        if review is None:
            raise KeyError(task_id)
        if review.status not in _TERMINAL_STATUSES:
            raise ValueError(
                f"Review is not in a terminal state: {review.status}. "
                "Export is only available for completed reviews."
            )

        execution = await self._store.get_execution(task_id)
        if execution is None:
            raise KeyError(task_id)

        findings = await self._store.list_findings(task_id)
        if not findings:
            raise ValueError("No findings to export for this review.")

        return await self._build_envelope_from_findings(
            task_id, review, execution, findings
        )

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
            source_excerpt = await self._build_source_snippet(
                execution, finding.primary_location
            )

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

        return FindingExportEnvelope(
            schema_version="1.0",
            exported_at=datetime.now(ZoneInfo("UTC")),
            review=ReviewExportMeta(
                task_id=review.task_id,
                repository_name=review.repository_name,
                scope_type=review.scope_type,
                base_oid=review.base_oid,
                head_oid=review.head_oid,
                selected_agent_versions=review.selected_agent_versions,
                status=review.status,
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

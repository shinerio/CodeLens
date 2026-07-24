"""Read complete pinned source for one persisted Finding."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codelens.findings.domain.models import Finding
from codelens.review.domain.ports import ReviewExecutionRecord


class _ReviewStorePort(Protocol):
    async def get_execution(self, task_id: str) -> ReviewExecutionRecord | None: ...

    async def list_findings(self, task_id: str) -> Sequence[Finding]: ...


class _RevisionReaderPort(Protocol):
    async def read_revision(self, repository: Path, revision: str, path: str) -> bytes: ...


@dataclass(frozen=True)
class FindingSourcePreview:
    """One complete source file anchored to a trusted Finding location.

    The content always comes from the review's pinned base or head revision,
    never from the mutable source workspace. Highlight bounds identify where the
    client should place the review annotation without replacing source lines.
    """

    path: str
    revision: str
    start_line: int
    end_line: int
    highlight_start_line: int
    highlight_end_line: int
    content: str


class FindingSourcePreviewService:
    """Serve source only after matching a persisted Finding and its pinned review revision."""

    def __init__(self, store: _ReviewStorePort, reader: _RevisionReaderPort) -> None:
        self._store = store
        self._reader = reader

    async def get(self, task_id: str, finding_id: str) -> FindingSourcePreview:
        execution = await self._store.get_execution(task_id)
        if execution is None:
            raise KeyError(task_id)
        findings = await self._store.list_findings(task_id)
        finding = next((item for item in findings if item.finding_id == finding_id), None)
        if finding is None:
            raise KeyError(finding_id)
        location = finding.primary_location
        revision = execution.base_oid if location.side == "old" else execution.head_oid
        source = await self._reader.read_revision(
            execution.repository_path, revision, location.path
        )
        content = source.decode("utf-8", errors="replace")
        lines = content.splitlines()
        return FindingSourcePreview(
            path=location.path,
            revision=revision,
            start_line=1,
            end_line=len(lines),
            highlight_start_line=location.start_line,
            highlight_end_line=location.end_line,
            content=content,
        )

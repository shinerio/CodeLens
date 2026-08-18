"""Read complete pinned source for one persisted Finding."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from codelens.findings.domain.models import Finding
from codelens.review.domain.ports import ReviewExecutionRecord


class _ReviewStorePort(Protocol):
    async def get_execution(self, task_id: str) -> ReviewExecutionRecord | None: ...

    async def list_findings(self, task_id: str) -> Sequence[Finding]: ...


class _RevisionReaderPort(Protocol):
    async def read_revision_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
    ) -> bytes | None: ...

    async def resolve_old_path_optional(
        self,
        repository: Path,
        base_revision: str,
        target_revision: str,
        path: str,
    ) -> str | None: ...


class _OverlayArtifactPort(Protocol):
    async def read_bytes(self, reference: str, expected_hash: str) -> bytes: ...


class _OverlaySourcePort(Protocol):
    async def read_overlay_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
        payload: bytes,
    ) -> bytes | None: ...


@dataclass(frozen=True)
class PinnedSourceVersion:
    """One complete file from a fixed Review revision."""

    path: str
    revision: str
    content: str


@dataclass(frozen=True)
class FindingSourcePreview:
    """One complete source file anchored to a trusted Finding location.

    Both available contents come from the Review's pinned revisions, never from
    the mutable source workspace. Added and deleted files have one missing side.
    """

    path: str
    base: PinnedSourceVersion | None
    target: PinnedSourceVersion | None
    highlight_side: Literal["old", "new"]
    highlight_start_line: int
    highlight_end_line: int


class FindingSourcePreviewService:
    """Serve source only after matching a persisted Finding and its pinned review revision."""

    def __init__(
        self,
        store: _ReviewStorePort,
        reader: _RevisionReaderPort,
        overlay_artifacts: _OverlayArtifactPort | None = None,
        overlay_source: _OverlaySourcePort | None = None,
    ) -> None:
        self._store = store
        self._reader = reader
        self._overlay_artifacts = overlay_artifacts
        self._overlay_source = overlay_source

    async def get(self, task_id: str, finding_id: str) -> FindingSourcePreview:
        execution = await self._store.get_execution(task_id)
        if execution is None:
            raise KeyError(task_id)
        findings = await self._store.list_findings(task_id)
        finding = next((item for item in findings if item.finding_id == finding_id), None)
        if finding is None:
            raise KeyError(finding_id)
        location = finding.primary_location
        highlight_side: Literal["old", "new"]
        if location.side == "old":
            highlight_side = "old"
        elif location.side == "new":
            highlight_side = "new"
        else:
            raise ValueError("Finding location has an unsupported source side")
        base_path = await self._reader.resolve_old_path_optional(
            execution.repository_path,
            execution.base_oid,
            execution.head_oid,
            location.path,
        )
        base_display_path = base_path if base_path is not None else location.path
        base_source = await self._reader.read_revision_optional(
            execution.repository_path,
            execution.base_oid,
            base_display_path,
        )
        target_source = await self._read_target(execution, location.path)
        return FindingSourcePreview(
            path=location.path,
            base=self._version(base_display_path, execution.base_oid, base_source),
            target=self._version(location.path, execution.head_oid, target_source),
            highlight_side=highlight_side,
            highlight_start_line=location.start_line,
            highlight_end_line=location.end_line,
        )

    async def _read_target(
        self,
        execution: ReviewExecutionRecord,
        path: str,
    ) -> bytes | None:
        if (
            execution.overlay_artifact_ref is None
            or execution.overlay_hash is None
            or self._overlay_artifacts is None
            or self._overlay_source is None
        ):
            return await self._reader.read_revision_optional(
                execution.repository_path,
                execution.head_oid,
                path,
            )
        payload = await self._overlay_artifacts.read_bytes(
            execution.overlay_artifact_ref,
            execution.overlay_hash,
        )
        return await self._overlay_source.read_overlay_optional(
            execution.repository_path,
            execution.head_oid,
            path,
            payload,
        )

    @staticmethod
    def _version(path: str, revision: str, source: bytes | None) -> PinnedSourceVersion | None:
        if source is None:
            return None
        return PinnedSourceVersion(
            path=path,
            revision=revision,
            content=source.decode("utf-8", errors="replace"),
        )

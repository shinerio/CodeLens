"""Resolve model-selected source excerpts against one immutable Review Snapshot."""

import json
from typing import Literal, Protocol

from codelens.findings.domain.models import SourceLocation
from codelens.review.infrastructure.line_resolver import (
    resolve_from_file_content,
    resolve_from_hunk,
)
from codelens.workspace.domain.models import ReviewSnapshot


class LocationOutsideChangedHunkError(ValueError):
    """Report that model-selected evidence does not belong to one changed hunk."""


class LocationEvidencePort(Protocol):
    """Expose the bounded frozen evidence required for location resolution."""

    @property
    def review_file_paths(self) -> tuple[str, ...]: ...

    async def read_diff_for_resolution(self, path: str) -> str: ...

    async def read_full_file(
        self,
        path: str,
        version: Literal["base", "current"],
    ) -> str: ...

    async def excerpt_identity(
        self,
        path: str,
        start_line: int,
        end_line: int,
        version: Literal["base", "current"],
    ) -> tuple[str, bool]: ...


class SnapshotLocationResolver:
    """Derive trusted line numbers, hashes, and hunk identity from model excerpts."""

    def __init__(
        self,
        snapshot: ReviewSnapshot,
        evidence: LocationEvidencePort,
    ) -> None:
        self._snapshot = snapshot
        self._evidence = evidence

    async def resolve(
        self,
        path: str,
        side: Literal["old", "new"],
        existing_code: str,
    ) -> tuple[SourceLocation, str]:
        """Resolve one exact changed excerpt or reject it without trusting model line data."""

        if path not in self._evidence.review_file_paths:
            raise ValueError("location path is outside this Review")
        diff_result = json.loads(await self._evidence.read_diff_for_resolution(path))
        diff_text = diff_result.get("content", "")
        if not isinstance(diff_text, str):
            raise ValueError("Snapshot diff response is invalid")
        resolved = resolve_from_hunk(diff_text, existing_code, side=side)
        if resolved is None:
            file_content = await self._evidence.read_full_file(
                path,
                "base" if side == "old" else "current",
            )
            resolved = resolve_from_file_content(file_content, existing_code)
        if resolved is None:
            raise ValueError("existing_code cannot be resolved to a line range")
        start_line, end_line = resolved
        excerpt_hash, is_truncated = await self._evidence.excerpt_identity(
            path,
            start_line,
            end_line,
            "base" if side == "old" else "current",
        )
        if is_truncated:
            raise ValueError("location cannot be resolved to a complete frozen excerpt")
        matching_hunks = tuple(
            hunk
            for hunk in self._snapshot.change_index.hunks
            if hunk.path == path
            and hunk.side == side
            and start_line >= hunk.start_line
            and end_line <= hunk.end_line
        )
        if len(matching_hunks) != 1:
            raise LocationOutsideChangedHunkError(
                f"existing_code must quote only consecutive changed {side}-side lines "
                "without diff markers or unchanged context lines"
            )
        entry = next(
            (item for item in self._snapshot.manifest.entries if item.path == path),
            None,
        )
        if entry is None:
            raise ValueError("location path is outside the frozen Snapshot")
        return (
            SourceLocation(
                path=path,
                start_line=start_line,
                end_line=end_line,
                side=side,
                excerpt_hash=excerpt_hash,
                is_deleted=entry.kind == "deleted",
            ),
            matching_hunks[0].hunk_id,
        )

"""Contained filesystem adapters for frozen review context."""

import asyncio
import hashlib
import os
from pathlib import Path, PurePosixPath

from codelens.review.application.context_builder import CandidateSummary, SnapshotRead
from codelens.workspace.domain.models import ReviewSnapshot, SnapshotEntry


def _normalized_relative(path: str) -> bool:
    candidate = PurePosixPath(path)
    return bool(
        path
        and "\0" not in path
        and "\\" not in path
        and not candidate.is_absolute()
        and ".." not in candidate.parts
        and candidate.as_posix() == path
    )


def _read_entry(root: Path, entry: SnapshotEntry) -> bytes:
    absolute = root / entry.path
    if entry.kind == "deleted":
        return b""
    if entry.kind == "symlink":
        return os.readlink(absolute).encode()
    resolved = absolute.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Snapshot context path escapes its worktree")
    return absolute.read_bytes()


class FilesystemSnapshotContextAdapter:
    """Summarize and read only hash-verified paths in an owned Snapshot."""

    async def summarize(self, snapshot: ReviewSnapshot) -> tuple[CandidateSummary, ...]:
        """Rank visible entries from frozen metadata without opening their bodies."""

        visible = {*snapshot.manifest.target_paths, *snapshot.manifest.context_paths}
        return tuple(
            CandidateSummary(
                path=entry.path,
                start_line=1,
                end_line=2_147_483_647,
                side="new",
                estimated_tokens=max(1, (entry.size_bytes + 3) // 4),
                priority=100 if entry.origin == "target" else 10,
                reason="review_target" if entry.origin == "target" else "snapshot_context",
                trust_label="changed_code" if entry.origin == "target" else "repository_context",
                content_hash=entry.content_hash,
                is_deleted=entry.kind == "deleted",
            )
            for entry in snapshot.manifest.entries
            if entry.path in visible
        )

    async def read(
        self,
        snapshot: ReviewSnapshot,
        path: str,
        start_line: int,
        end_line: int,
        side: str,
        max_bytes: int,
    ) -> SnapshotRead:
        """Read one new-side line range after containment and full-entry hash checks."""

        if (
            not _normalized_relative(path)
            or side != "new"
            or start_line < 1
            or end_line < start_line
            or max_bytes < 1
        ):
            raise ValueError("Snapshot context read is invalid")
        entry = next(
            (
                candidate
                for candidate in snapshot.manifest.entries
                if candidate.path == path and candidate.origin in {"target", "context"}
            ),
            None,
        )
        if entry is None:
            raise ValueError("Snapshot context path is not visible")
        payload = await asyncio.to_thread(_read_entry, snapshot.worktree.root, entry)
        if hashlib.sha256(payload).hexdigest() != entry.content_hash:
            raise ValueError("Snapshot context content changed")
        selected = b"".join(payload.splitlines(keepends=True)[start_line - 1 : end_line])
        content_hash = hashlib.sha256(selected).hexdigest()
        return SnapshotRead(
            content=selected[:max_bytes],
            content_hash=content_hash,
            truncated=len(selected) > max_bytes,
        )

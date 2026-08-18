"""Contained filesystem reader for frozen Review Snapshot evidence."""

import asyncio
import hashlib
import os
from pathlib import Path, PurePosixPath

from codelens.review.domain.ports import SnapshotRead
from codelens.workspace.domain.models import ReviewSnapshot, SnapshotEntry
from codelens.workspace.infrastructure.git_cli import GitCli


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
    """Read the full content of one snapshot entry for hash verification.

    The entire file is read so its content hash can be verified against the
    frozen snapshot. Excerpt-level truncation is applied by the caller after
    line-range extraction, not here.
    """
    absolute = root / entry.path
    if entry.kind == "deleted":
        return b""
    if entry.kind == "symlink":
        return os.readlink(absolute).encode("utf-8")
    resolved = absolute.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Snapshot context path escapes its worktree")
    with absolute.open("rb") as stream:
        return stream.read()


class FilesystemSnapshotReader:
    """Read hash-verified current or pinned-base excerpts from one Snapshot."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def read(
        self,
        snapshot: ReviewSnapshot,
        path: str,
        start_line: int,
        end_line: int,
        side: str,
        max_bytes: int,
    ) -> SnapshotRead:
        if (
            not _normalized_relative(path)
            or side not in {"old", "new"}
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
        if side == "old":
            payload = await self._git.read_revision(
                snapshot.worktree.root,
                snapshot.target.base_oid,
                path,
            )
        else:
            payload = await asyncio.to_thread(_read_entry, snapshot.worktree.root, entry)
            if hashlib.sha256(payload).hexdigest() != entry.content_hash:
                raise ValueError("Snapshot context content changed")
        selected = b"".join(payload.splitlines(keepends=True)[start_line - 1 : end_line])
        return SnapshotRead(
            content=selected[:max_bytes],
            content_hash=hashlib.sha256(selected).hexdigest(),
            truncated=len(selected) > max_bytes,
        )

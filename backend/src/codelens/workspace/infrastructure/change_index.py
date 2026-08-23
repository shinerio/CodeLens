import asyncio
import hashlib
import os
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.models import (
    ChangedHunk,
    ChangeIndex,
    ReviewFileChange,
    ReviewScopeType,
    TaskWorktree,
)
from codelens.workspace.infrastructure.git_cli import GitCli

_WHITESPACE_RE = re.compile(rb"[ \t\r\n\f\v]+")

_HUNK_HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_GIT_PATH_ESCAPES = {
    "a": b"\a",
    "b": b"\b",
    "t": b"\t",
    "n": b"\n",
    "v": b"\v",
    "f": b"\f",
    "r": b"\r",
    "\\": b"\\",
    '"': b'"',
}


def _read_payload(path: Path) -> bytes | None:
    try:
        if path.is_symlink():
            return os.readlink(path).encode("utf-8")
        return path.read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return None


def _read_lines(path: Path) -> tuple[bytes, ...]:
    payload = _read_payload(path)
    if payload is None or b"\0" in payload:
        return ()
    return tuple(payload.splitlines(keepends=True))


def _normalize_whitespace(line: bytes) -> bytes:
    """Strip all whitespace to detect lines that differ only in spacing.

    Collapses every run of spaces, tabs, carriage returns, newlines, form
    feeds, and vertical tabs into nothing.  Two lines that produce the same
    normalized form differ only in whitespace characters and are therefore
    not substantive code changes.
    """

    return _WHITESPACE_RE.sub(b"", line)


def _is_whitespace_only_change(old_excerpt: bytes, new_excerpt: bytes) -> bool:
    """Return True when old and new excerpts differ only in whitespace.

    Compares the set of non-whitespace content on each side.  If every
    old-side line has a matching new-side line (ignoring leading/trailing
    whitespace, blank lines, and ``\\r\\n`` vs ``\\n`` line endings), the
    change is purely cosmetic and should not consume Review tokens.
    """

    old_stripped = sorted(
        norm
        for line in old_excerpt.splitlines()
        if (norm := _normalize_whitespace(line))
    )
    new_stripped = sorted(
        norm
        for line in new_excerpt.splitlines()
        if (norm := _normalize_whitespace(line))
    )
    return old_stripped == new_stripped


def _normalize_path(raw_path: bytes) -> str:
    path = raw_path.decode("utf-8", errors="strict")
    candidate = PurePosixPath(path)
    if (
        not path
        or "\0" in path
        or "\\" in path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.as_posix() != path
    ):
        raise InvalidRepositoryError("Git returned an unsafe change path")
    return path


def _decode_git_quoted_path(raw_path: str) -> bytes:
    """Decode Git's documented C-style path quoting before trust-boundary validation."""

    if not raw_path.startswith('"'):
        return raw_path.encode("utf-8")
    if len(raw_path) < 2 or not raw_path.endswith('"'):
        raise InvalidRepositoryError("Git returned a malformed quoted change path")

    decoded = bytearray()
    body = raw_path[1:-1]
    offset = 0
    while offset < len(body):
        character = body[offset]
        if character != "\\":
            decoded.extend(character.encode("utf-8"))
            offset += 1
            continue
        if offset + 1 >= len(body):
            raise InvalidRepositoryError("Git returned a malformed quoted change path")
        escape = body[offset + 1]
        escaped_byte = _GIT_PATH_ESCAPES.get(escape)
        if escaped_byte is not None:
            decoded.extend(escaped_byte)
            offset += 2
            continue
        octal = body[offset + 1 : offset + 4]
        if len(octal) != 3 or any(digit not in "01234567" for digit in octal):
            raise InvalidRepositoryError("Git returned an unsupported quoted change path")
        decoded.append(int(octal, 8))
        offset += 4
    return bytes(decoded)


def _diff_header_path(raw_path: str) -> str | None:
    decoded = _decode_git_quoted_path(raw_path)
    if decoded == b"/dev/null":
        return None
    value = decoded[2:] if decoded.startswith((b"a/", b"b/")) else decoded
    return _normalize_path(value)


def _parse_name_status(output: bytes) -> tuple[ReviewFileChange, ...]:
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changes: list[ReviewFileChange] = []
    offset = 0
    while offset < len(fields):
        status = fields[offset].decode("ascii", errors="strict")
        offset += 1
        code = status[:1]
        if code == "R":
            if offset + 2 > len(fields):
                raise InvalidRepositoryError("unexpected Git rename status")
            old_path = _normalize_path(fields[offset])
            path = _normalize_path(fields[offset + 1])
            changes.append(ReviewFileChange(path, "renamed", old_path=old_path))
            offset += 2
            continue
        if code not in {"A", "D", "M", "T"} or offset >= len(fields):
            raise InvalidRepositoryError("unsupported Git file change status")
        path = _normalize_path(fields[offset])
        offset += 1
        change_type: Literal["added", "modified", "deleted"]
        if code == "A":
            change_type = "added"
        elif code == "D":
            change_type = "deleted"
        else:
            change_type = "modified"
        changes.append(ReviewFileChange(path, change_type))
    return tuple(changes)


class GitChangeIndexBuilder:
    """Build deterministic file changes and hunk identities from a frozen worktree."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def build(
        self,
        worktree: TaskWorktree,
        base_oid: str,
        candidate_paths: tuple[str, ...],
        scope_type: ReviewScopeType,
    ) -> ChangeIndex:
        """Index every target's typed file change and new- or old-side ranges."""

        target_set = set(candidate_paths)
        if scope_type == "full":
            return await self._build_full_scope(worktree, target_set)
        status_result = await self._git.run(
            worktree.root,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "--no-ext-diff",
            "--no-textconv",
            base_oid,
            "--",
        )
        changes = [
            change
            for change in _parse_name_status(status_result.stdout)
            if change.path in target_set
            or (change.old_path is not None and change.old_path in target_set)
        ]
        covered_paths = {
            path
            for change in changes
            for path in (change.path, change.old_path)
            if path is not None
        }
        untracked_result = await self._git.run(
            worktree.root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        untracked_paths = tuple(
            path
            for raw_path in untracked_result.stdout.split(b"\0")
            if raw_path
            if (path := _normalize_path(raw_path)) in target_set
        )
        for path in untracked_paths:
            if path not in covered_paths:
                changes.append(ReviewFileChange(path, "added"))
                covered_paths.add(path)
        if not target_set.issubset(covered_paths):
            raise InvalidRepositoryError("Review target has no reliable file change metadata")

        diff_result = await self._git.run(
            worktree.root,
            "diff",
            "--unified=0",
            "--find-renames",
            "--no-ext-diff",
            "--no-textconv",
            base_oid,
            "--",
        )
        hunks, whitespace_only_paths = await self._parse_hunks(
            worktree, diff_result.stdout, target_set, base_oid,
        )
        hunk_paths = {hunk.path for hunk in hunks}
        for path in untracked_paths:
            if path in hunk_paths:
                continue
            file_lines = await asyncio.to_thread(_read_lines, worktree.root / path)
            if not file_lines:
                continue
            excerpt = b"".join(file_lines)
            hunks.append(self._hunk(path, 1, len(file_lines), "new", excerpt))
        changes = [change for change in changes if change.path not in whitespace_only_paths]
        hunks = [hunk for hunk in hunks if hunk.path not in whitespace_only_paths]
        return ChangeIndex(
            hunks=tuple(
                sorted(
                    hunks,
                    key=lambda item: (
                        item.path,
                        item.start_line,
                        item.end_line,
                        item.side,
                        item.hunk_id,
                    ),
                )
            ),
            files=tuple(sorted(changes, key=lambda item: item.path)),
        )

    async def _build_full_scope(
        self,
        worktree: TaskWorktree,
        candidate_paths: set[str],
    ) -> ChangeIndex:
        """Treat the final full-repository Snapshot as added from an empty baseline."""

        changes: list[ReviewFileChange] = []
        hunks: list[ChangedHunk] = []
        for path in sorted(candidate_paths):
            payload = await asyncio.to_thread(_read_payload, worktree.root / path)
            if payload is None:
                changes.append(ReviewFileChange(path, "deleted"))
                continue
            changes.append(ReviewFileChange(path, "added"))
            if not payload or b"\0" in payload:
                continue
            lines = tuple(payload.splitlines(keepends=True))
            if lines:
                hunks.append(self._hunk(path, 1, len(lines), "new", b"".join(lines)))
        return ChangeIndex(hunks=tuple(hunks), files=tuple(changes))

    async def _parse_hunks(
        self,
        worktree: TaskWorktree,
        output: bytes,
        candidate_paths: set[str],
        base_oid: str,
    ) -> tuple[list[ChangedHunk], set[str]]:
        """Parse unified diff hunks and identify whitespace-only modified files.

        Returns a tuple of (hunks, whitespace_only_paths) where
        whitespace_only_paths contains every ``modified`` file whose old
        and new hunks differ only in whitespace (indentation, blank lines,
        ``\\r\\n`` vs ``\\n``).  Callers remove these files from the
        ChangeIndex so they do not consume Review tokens.
        """

        lines = output.decode("utf-8", errors="replace").splitlines()
        old_path: str | None = None
        new_path: str | None = None
        hunks: list[ChangedHunk] = []
        old_lines_by_path: dict[str, tuple[bytes, ...]] = {}
        new_lines_by_path: dict[str, tuple[bytes, ...]] = {}
        # Track old/new excerpts per path to detect whitespace-only changes.
        old_excerpts_by_path: dict[str, list[bytes]] = {}
        new_excerpts_by_path: dict[str, list[bytes]] = {}
        for line in lines:
            if line.startswith("--- "):
                old_path = _diff_header_path(line[4:])
            elif line.startswith("+++ "):
                new_path = _diff_header_path(line[4:])
            elif line.startswith("@@ "):
                path = new_path if new_path is not None else old_path
                if path is None or path not in candidate_paths:
                    continue
                match = _HUNK_HEADER.match(line)
                if match is None:
                    raise InvalidRepositoryError("unexpected unified diff hunk header")
                old_start, old_count_raw, new_start, new_count_raw = match.groups()
                old_count = int(old_count_raw or "1")
                new_count = int(new_count_raw or "1")
                if old_count > 0:
                    if old_path is None:
                        raise InvalidRepositoryError("old-side hunk has no source path")
                    old_line = int(old_start)
                    old_lines = old_lines_by_path.get(old_path)
                    if old_lines is None:
                        old_blob = await self._git.read_revision_optional(
                            worktree.root,
                            base_oid,
                            old_path,
                        )
                        if old_blob is None:
                            old_lines = ()
                        else:
                            old_lines = tuple(old_blob.splitlines(keepends=True))
                        old_lines_by_path[old_path] = old_lines
                    if old_lines and old_line <= len(old_lines):
                        old_excerpt = b"".join(old_lines[old_line - 1 : old_line + old_count - 1])
                        hunks.append(
                            self._hunk(
                                path,
                                old_line,
                                old_line + old_count - 1,
                                "old",
                                old_excerpt,
                            )
                        )
                        old_excerpts_by_path.setdefault(path, []).append(old_excerpt)
                if new_count > 0:
                    start_line = int(new_start)
                    end_line = start_line + new_count - 1
                    file_lines = new_lines_by_path.get(path)
                    if file_lines is None:
                        file_lines = await asyncio.to_thread(_read_lines, worktree.root / path)
                        new_lines_by_path[path] = file_lines
                    excerpt = b"".join(file_lines[start_line - 1 : end_line])
                    hunks.append(self._hunk(path, start_line, end_line, "new", excerpt))
                    new_excerpts_by_path.setdefault(path, []).append(excerpt)

        whitespace_only_paths: set[str] = set()
        all_paths = set(old_excerpts_by_path) | set(new_excerpts_by_path)
        for path in all_paths:
            old_combined = b"".join(old_excerpts_by_path.get(path, []))
            new_combined = b"".join(new_excerpts_by_path.get(path, []))
            # Only filter modified files — added or deleted files with empty
            # content are genuine changes, not whitespace-only noise.
            if not old_combined and not new_combined:
                continue
            if _is_whitespace_only_change(old_combined, new_combined):
                whitespace_only_paths.add(path)
        return hunks, whitespace_only_paths

    @staticmethod
    def _hunk(
        path: str,
        start_line: int,
        end_line: int,
        side: Literal["old", "new"],
        excerpt: bytes,
    ) -> ChangedHunk:
        excerpt_hash = hashlib.sha256(excerpt).hexdigest()
        identity = f"{path}\0{side}\0{start_line}\0{end_line}\0{excerpt_hash}"
        return ChangedHunk(
            hunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            path=path,
            start_line=start_line,
            end_line=end_line,
            side=side,
            excerpt_hash=excerpt_hash,
        )

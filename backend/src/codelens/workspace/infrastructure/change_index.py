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
        target_paths: tuple[str, ...],
        scope_type: ReviewScopeType,
    ) -> ChangeIndex:
        """Index every target's typed file change and new- or old-side ranges."""

        target_set = set(target_paths)
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
        hunks = await self._parse_hunks(worktree, diff_result.stdout, target_set)
        hunk_paths = {hunk.path for hunk in hunks}
        for path in untracked_paths:
            if path in hunk_paths:
                continue
            file_lines = await asyncio.to_thread(_read_lines, worktree.root / path)
            if not file_lines:
                continue
            excerpt = b"".join(file_lines)
            hunks.append(self._hunk(path, 1, len(file_lines), "new", excerpt))
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
        target_paths: set[str],
    ) -> ChangeIndex:
        """Treat the final full-repository Snapshot as added from an empty baseline."""

        changes: list[ReviewFileChange] = []
        hunks: list[ChangedHunk] = []
        for path in sorted(target_paths):
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
        target_paths: set[str],
    ) -> list[ChangedHunk]:
        lines = output.decode("utf-8", errors="replace").splitlines()
        old_path: str | None = None
        new_path: str | None = None
        hunks: list[ChangedHunk] = []
        for line in lines:
            if line.startswith("--- "):
                old_path = _diff_header_path(line[4:])
            elif line.startswith("+++ "):
                new_path = _diff_header_path(line[4:])
            elif line.startswith("@@ "):
                path = new_path if new_path is not None else old_path
                if path is None or path not in target_paths:
                    continue
                match = _HUNK_HEADER.match(line)
                if match is None:
                    raise InvalidRepositoryError("unexpected unified diff hunk header")
                old_start, old_count_raw, new_start, new_count_raw = match.groups()
                old_count = int(old_count_raw or "1")
                new_count = int(new_count_raw or "1")
                if new_count > 0:
                    side: Literal["old", "new"] = "new"
                    start_line = int(new_start)
                    end_line = start_line + new_count - 1
                    file_lines = await asyncio.to_thread(_read_lines, worktree.root / path)
                    excerpt = b"".join(file_lines[start_line - 1 : end_line])
                else:
                    side = "old"
                    start_line = int(old_start)
                    end_line = start_line + max(old_count, 1) - 1
                    excerpt = line.encode("utf-8")
                hunks.append(self._hunk(path, start_line, end_line, side, excerpt))
        return hunks

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

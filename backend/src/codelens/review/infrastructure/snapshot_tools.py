"""Read-only, bounded tools over a single frozen review Snapshot.

The model never receives a worktree path. Every operation is constrained to a
manifest entry and validates its content hash before returning repository text.
"""

import asyncio
import difflib
import fnmatch
import hashlib
import json
import multiprocessing
import os
import re
from functools import cache
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import PurePosixPath
from typing import Annotated, Literal

from agents import Tool, function_tool
from pydantic import Field

from codelens.review.application.review_scope import ReviewFileInput, build_review_files
from codelens.review.infrastructure.tool_contract import reject_unknown_arguments
from codelens.workspace.domain.models import ReviewSnapshot, SnapshotEntry
from codelens.workspace.infrastructure.git_cli import GitCli

_MAX_RESULTS = 200
_MAX_READ_BYTES = 64 * 1024
_MAX_SCAN_BYTES = 1024 * 1024
_MAX_LINES = 500
_MAX_PATH_CHARS = 1024
_MAX_PATTERN_CHARS = 512
_DEFAULT_REGEX_TIMEOUT_SECONDS = 5.0
_REGEX_WORKER_STARTUP_TIMEOUT_SECONDS = 15.0
_REGEX_WORKER_READY = "regex_worker_ready"
_FileVersion = Literal["current", "base", "head"]
_ModelPath = Annotated[str, Field(max_length=_MAX_PATH_CHARS)]
_ModelPattern = Annotated[str, Field(min_length=1, max_length=_MAX_PATTERN_CHARS)]
_ModelLine = Annotated[int, Field(ge=1)]


def _matches_posix_path_glob(path: str, pattern: str) -> bool:
    """Match a complete relative POSIX path with segment-aware `*` and recursive `**`."""

    path_parts = PurePosixPath(path).parts
    pattern_parts = PurePosixPath(pattern).parts

    @cache
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        pattern_part = pattern_parts[pattern_index]
        if pattern_part == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], pattern_part)
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def _regex_search_worker(
    sender: Connection,
    pattern: str,
    files: tuple[tuple[str, str], ...],
) -> None:
    """Run untrusted regular-expression matching in a terminable child process."""

    try:
        sender.send(_REGEX_WORKER_READY)
        expression = re.compile(pattern)
        matches: list[dict[str, object]] = []
        for path, text in files:
            for line_number, line in enumerate(text.splitlines(), start=1):
                if expression.search(line):
                    matches.append({"path": path, "line": line_number, "text": line[:200]})
                    if len(matches) >= _MAX_RESULTS:
                        sender.send((matches, True))
                        return
        sender.send((matches, False))
    finally:
        sender.close()


def _terminate_process(process: BaseProcess) -> None:
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=1.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)


def _search_regular_expression(
    pattern: str,
    files: tuple[tuple[str, str], ...],
    timeout_seconds: float,
) -> tuple[list[dict[str, object]], bool]:
    """Bound worker startup separately, then terminate regex evaluation at its deadline."""

    process_context = multiprocessing.get_context("spawn")
    receiver, sender = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_regex_search_worker,
        args=(sender, pattern, files),
        daemon=True,
    )
    try:
        process.start()
        sender.close()
        if not receiver.poll(_REGEX_WORKER_STARTUP_TIMEOUT_SECONDS):
            raise ValueError("grep worker failed to start")
        if receiver.recv() != _REGEX_WORKER_READY:
            raise ValueError("grep worker returned an invalid readiness signal")
        if not receiver.poll(timeout_seconds):
            raise ValueError("grep pattern evaluation timed out")
        message: object = receiver.recv()
        if (
            not isinstance(message, tuple)
            or len(message) != 2
            or not isinstance(message[0], list)
            or not isinstance(message[1], bool)
        ):
            raise ValueError("grep worker returned an invalid result")
        return message[0], message[1]
    except EOFError:
        raise ValueError("grep worker terminated without a result") from None
    finally:
        receiver.close()
        sender.close()
        _terminate_process(process)


class FilesystemReviewTools:
    """Serve review evidence from one manifest-verified Snapshot.

    All methods return JSON so they can be attached unchanged to an agent
    function-tool adapter. The mutable call counter is scoped to one agent run;
    it prevents a tool-using agent from scanning an unbounded repository.
    """

    def __init__(
        self,
        snapshot: ReviewSnapshot,
        git: GitCli,
        *,
        max_tool_calls: int | None,
        regex_timeout_seconds: float = _DEFAULT_REGEX_TIMEOUT_SECONDS,
    ) -> None:
        if max_tool_calls is not None and max_tool_calls <= 0:
            raise ValueError("tool call budget must be positive when configured")
        if regex_timeout_seconds <= 0:
            raise ValueError("regex timeout must be positive")
        self._snapshot = snapshot
        self._git = git
        self._root = snapshot.worktree.root.resolve()
        self._remaining_calls = max_tool_calls
        self._regex_timeout_seconds = regex_timeout_seconds
        self._entries = {
            entry.path: entry
            for entry in snapshot.manifest.entries
            if entry.origin in {"target", "context"}
        }
        review_files = build_review_files(
            snapshot,
            max_files=max(1, len(snapshot.change_index.files)),
            max_ranges=max(1, len(snapshot.change_index.hunks)),
        )
        self._review_files_by_path = {item.path: item for item in review_files}
        self._evidence_viewed_paths: set[str] = set()

    async def find_files(self, path: str = "", pattern: str = "**") -> str:
        """Find visible files below a directory using a relative POSIX glob pattern."""

        self._consume()
        normalized_pattern = PurePosixPath(pattern)
        if (
            not pattern
            or len(pattern) > _MAX_PATTERN_CHARS
            or pattern.startswith("/")
            or "\\" in pattern
            or ".." in normalized_pattern.parts
            or normalized_pattern.as_posix() != pattern
        ):
            raise ValueError("file pattern is invalid")
        prefix = self._directory_prefix(path)
        paths = [
            candidate
            for candidate in sorted(self._entries)
            if candidate.startswith(prefix)
            and _matches_posix_path_glob(candidate[len(prefix) :], pattern)
        ]
        return self._json({"paths": paths[:_MAX_RESULTS], "truncated": len(paths) > _MAX_RESULTS})

    async def grep(self, pattern: str) -> str:
        """Search visible UTF-8 text with a bounded regular expression."""

        self._consume()
        if len(pattern) > _MAX_PATTERN_CHARS:
            raise ValueError("grep pattern is invalid")
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError("grep pattern is invalid") from error
        files: list[tuple[str, str]] = []
        scanned = 0
        for path, entry in sorted(self._entries.items()):
            payload = await self._payload(entry)
            if b"\0" in payload:
                continue
            scanned += len(payload)
            if scanned > _MAX_SCAN_BYTES:
                break
            text = payload.decode("utf-8", errors="replace")
            files.append((path, text))
        matches, result_truncated = await asyncio.to_thread(
            _search_regular_expression,
            pattern,
            tuple(files),
            self._regex_timeout_seconds,
        )
        return self._json(
            {
                "matches": matches,
                "truncated": result_truncated or scanned > _MAX_SCAN_BYTES,
            }
        )

    async def read_file(
        self,
        path: str,
        start_line: int,
        end_line: int,
        version: _FileVersion = "current",
    ) -> str:
        """Read a bounded current, base, or head line range from a visible file."""

        self._consume()
        if version not in {"current", "base", "head"}:
            raise ValueError("file version is invalid")
        selected = await self._selected_file_lines(path, start_line, end_line, version)
        raw_content = selected[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
        content = self._add_line_prefixes(raw_content, start_line)
        if path in self._review_files_by_path:
            self._evidence_viewed_paths.add(path)
        return self._json(
            {
                "path": path,
                "version": version,
                "start_line": start_line,
                "end_line": end_line,
                "content": content,
                "truncated": len(selected) > _MAX_READ_BYTES,
            }
        )

    async def excerpt_identity(
        self,
        path: str,
        start_line: int,
        end_line: int,
        version: Literal["current", "base"] = "current",
    ) -> tuple[str, bool]:
        """Derive a bounded excerpt identity for backend Finding resolution only."""

        selected = await self._selected_file_lines(path, start_line, end_line, version)
        return hashlib.sha256(selected).hexdigest(), len(selected) > _MAX_READ_BYTES

    async def get_diff(self, path: str) -> str:
        """Read the bounded base-to-verified-current diff for one changed visible file."""

        self._consume()
        result = await self._get_diff(path)
        self._evidence_viewed_paths.add(path)
        return result

    async def read_diff_for_resolution(self, path: str) -> str:
        """Read a verified diff internally without recording model-visible evidence."""

        return await self._get_diff(path)

    async def _get_diff(self, path: str) -> str:
        """Build one verified diff without applying model-visible call accounting."""

        entry = self._entry(path)
        review_file = self._review_files_by_path.get(path)
        if review_file is None:
            raise ValueError("path is not a Review file")
        current_payload = await self._payload(entry)
        base_payload = (
            b""
            if review_file.change_type == "added"
            else await self._revision_payload(path, "base")
        )
        output = self._build_verified_diff(entry, review_file, base_payload, current_payload)
        content = output[:_MAX_READ_BYTES]
        return self._json(
            {
                "path": path,
                "content": content.decode("utf-8", errors="replace"),
                "truncated": len(output) > _MAX_READ_BYTES,
            }
        )

    @property
    def review_file_paths(self) -> tuple[str, ...]:
        """Return the stable canonical Review target paths."""

        return tuple(sorted(self._review_files_by_path))

    @property
    def evidence_viewed_paths(self) -> frozenset[str]:
        """Return Review paths exposed through successful model-visible evidence calls."""

        return frozenset(self._evidence_viewed_paths)

    def as_agent_tools(self, descriptions: dict[str, str]) -> list[Tool]:
        """Expose the stable read-only contract using startup-loaded descriptions."""

        @function_tool(
            name_override="find_files",
            description_override=descriptions["find_files"],
        )
        async def find_files_tool(path: _ModelPath, pattern: _ModelPattern) -> str:
            """Find visible files below a directory using a relative POSIX glob pattern."""

            return await self.find_files(path, pattern)

        @function_tool(
            name_override="grep",
            description_override=descriptions["grep"],
        )
        async def grep_tool(pattern: _ModelPattern) -> str:
            """Search visible Snapshot text with a regular expression."""

            return await self.grep(pattern)

        @function_tool(
            name_override="read_file",
            description_override=descriptions["read_file"],
        )
        async def read_file_tool(
            path: _ModelPath,
            start_line: _ModelLine,
            end_line: _ModelLine,
            version: _FileVersion,
        ) -> str:
            """Read a bounded current, base, or head range from a visible file."""

            return await self.read_file(path, start_line, end_line, version)

        @function_tool(
            name_override="get_diff",
            description_override=descriptions["get_diff"],
        )
        async def get_diff_tool(path: _ModelPath) -> str:
            """Read the base-to-current diff for a changed visible file."""

            return await self.get_diff(path)

        return [
            reject_unknown_arguments(find_files_tool),
            reject_unknown_arguments(grep_tool),
            reject_unknown_arguments(read_file_tool),
            reject_unknown_arguments(get_diff_tool),
        ]

    def _consume(self) -> None:
        if self._remaining_calls is not None and self._remaining_calls <= 0:
            raise ValueError("tool call budget exceeded")
        if self._remaining_calls is not None:
            self._remaining_calls -= 1

    def _entry(self, path: str) -> SnapshotEntry:
        if not self._is_normalized_relative(path) or path not in self._entries:
            raise ValueError("Snapshot context path is not visible")
        return self._entries[path]

    async def _payload(self, entry: SnapshotEntry) -> bytes:
        absolute = self._root / entry.path
        if entry.kind == "deleted":
            if await asyncio.to_thread(os.path.lexists, absolute):
                raise ValueError("Snapshot context content changed")
            if hashlib.sha256(b"").hexdigest() != entry.content_hash:
                raise ValueError("Snapshot context content changed")
            return b""
        if entry.kind == "symlink":
            target = await asyncio.to_thread(os.readlink, absolute)
            payload = target.encode("utf-8")
        else:
            resolved = absolute.resolve()
            if not resolved.is_relative_to(self._root):
                raise ValueError("Snapshot context path escapes its worktree")
            payload = await asyncio.to_thread(absolute.read_bytes)
        if hashlib.sha256(payload).hexdigest() != entry.content_hash:
            raise ValueError("Snapshot context content changed")
        return payload

    @staticmethod
    def _build_verified_diff(
        entry: SnapshotEntry,
        review_file: ReviewFileInput,
        base_payload: bytes,
        current_payload: bytes,
    ) -> bytes:
        """Build diff text only from pinned base bytes and hash-verified current bytes."""

        change_type = review_file.change_type
        old_path = review_file.old_path
        path = review_file.path
        base_path = old_path or path
        header = f"diff --git a/{base_path} b/{path}\n"
        metadata = ""
        if change_type == "added":
            mode = "120000" if entry.kind == "symlink" else f"100{entry.mode & 0o777:o}"
            metadata = f"new file mode {mode}\n"
        elif change_type == "renamed":
            similarity = "similarity index 100%\n" if base_payload == current_payload else ""
            metadata = f"{similarity}rename from {base_path}\nrename to {path}\n"

        if b"\0" in base_payload or b"\0" in current_payload:
            before = "/dev/null" if change_type == "added" else f"a/{base_path}"
            after = "/dev/null" if change_type == "deleted" else f"b/{path}"
            return f"{header}{metadata}Binary files {before} and {after} differ\n".encode()

        before_lines = base_payload.decode("utf-8", errors="replace").splitlines(keepends=True)
        after_lines = current_payload.decode("utf-8", errors="replace").splitlines(keepends=True)
        body = "".join(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile="/dev/null" if change_type == "added" else f"a/{base_path}",
                tofile="/dev/null" if change_type == "deleted" else f"b/{path}",
                n=3,
            )
        )
        if not body and not metadata:
            return b""
        return f"{header}{metadata}{body}".encode()

    async def _selected_file_lines(
        self,
        path: str,
        start_line: int,
        end_line: int,
        version: _FileVersion = "current",
    ) -> bytes:
        if start_line < 1 or end_line < start_line or end_line - start_line >= _MAX_LINES:
            raise ValueError("line range is invalid")
        entry = self._entry(path)
        if version == "current":
            payload = await self._payload(entry)
        else:
            await self._payload(entry)
            payload = await self._revision_payload(path, version)
        if b"\0" in payload:
            raise ValueError("Snapshot file is binary")
        return b"".join(payload.splitlines(keepends=True)[start_line - 1 : end_line])

    async def _revision_payload(self, path: str, version: Literal["base", "head"]) -> bytes:
        review_file = self._review_files_by_path.get(path)
        if review_file is not None and (
            (review_file.change_type == "added" and version == "base")
            or (review_file.change_type == "deleted" and version == "head")
        ):
            raise ValueError("path is unavailable in version")
        revision_path = (
            review_file.old_path
            if version == "base" and review_file is not None and review_file.old_path is not None
            else path
        )
        oid = (
            self._snapshot.target.base_oid if version == "base" else self._snapshot.target.head_oid
        )
        result = await self._git.run(
            self._root,
            "show",
            f"{oid}:{revision_path}",
            ok_codes=(0, 128),
        )
        if result.returncode != 0:
            raise ValueError("path is unavailable in version")
        return result.stdout

    @staticmethod
    def _is_normalized_relative(path: str) -> bool:
        candidate = PurePosixPath(path)
        return bool(
            path
            and "\0" not in path
            and "\\" not in path
            and not candidate.is_absolute()
            and ".." not in candidate.parts
            and candidate.as_posix() == path
        )

    @classmethod
    def _directory_prefix(cls, path: str) -> str:
        if not path:
            return ""
        if len(path) > _MAX_PATH_CHARS:
            raise ValueError("directory path is invalid")
        normalized = path[:-1] if path.endswith("/") else path
        if not cls._is_normalized_relative(normalized):
            raise ValueError("directory path is invalid")
        return f"{normalized}/"

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _add_line_prefixes(content: str, start_line: int) -> str:
        """Add line number prefixes to content in format: 'linenum|content'."""
        lines = content.split("\n")
        prefixed = [f"{start_line + i}|{line}" for i, line in enumerate(lines) if line]
        return "\n".join(prefixed)

    async def read_full_file(
        self,
        path: str,
        version: Literal["current", "base"] = "current",
    ) -> str:
        """Read an entire selected-side file for line resolution fallback.

        Unlike read_file, this has no line range limits and is not exposed as an agent tool.
        Used internally by the line resolver when hunk matching fails.
        """
        entry = self._entry(path)
        current_payload = await self._payload(entry)
        payload = (
            current_payload
            if version == "current"
            else await self._revision_payload(path, "base")
        )
        if b"\0" in payload:
            raise ValueError("Snapshot file is binary")
        return payload.decode("utf-8", errors="replace")

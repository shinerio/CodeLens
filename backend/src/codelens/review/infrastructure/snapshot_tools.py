"""Read-only, bounded tools over a single frozen review Snapshot.

The model never receives a worktree path. Every operation is constrained to a
manifest entry and validates its content hash before returning repository text.
"""

import asyncio
import fnmatch
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Literal

from agents import Tool, function_tool

from codelens.workspace.domain.models import ReviewSnapshot, SnapshotEntry
from codelens.workspace.infrastructure.git_cli import GitCli

_MAX_RESULTS = 200
_MAX_READ_BYTES = 64 * 1024
_MAX_SCAN_BYTES = 1024 * 1024
_MAX_LINES = 500


class FilesystemReviewTools:
    """Serve review evidence from one manifest-verified Snapshot.

    All methods return JSON so they can be attached unchanged to an agent
    function-tool adapter. The mutable call counter is scoped to one agent run;
    it prevents a tool-using agent from scanning an unbounded repository.
    """

    def __init__(self, snapshot: ReviewSnapshot, git: GitCli, *, max_tool_calls: int) -> None:
        if max_tool_calls <= 0:
            raise ValueError("tool call budget must be positive")
        self._snapshot = snapshot
        self._git = git
        self._remaining_calls = max_tool_calls
        self._entries = {
            entry.path: entry
            for entry in snapshot.manifest.entries
            if entry.origin in {"target", "context"}
        }

    async def explore(self, path: str = "") -> str:
        """List visible Snapshot files beneath one normalized relative directory."""

        self._consume()
        prefix = self._directory_prefix(path)
        paths = [candidate for candidate in sorted(self._entries) if candidate.startswith(prefix)]
        return self._json({"paths": paths[:_MAX_RESULTS], "truncated": len(paths) > _MAX_RESULTS})

    async def glob(self, pattern: str) -> str:
        """Find manifest-visible paths using a bounded POSIX glob pattern."""

        self._consume()
        if (
            not pattern
            or pattern.startswith("/")
            or "\\" in pattern
            or ".." in PurePosixPath(pattern).parts
        ):
            raise ValueError("glob pattern is invalid")
        paths = [path for path in sorted(self._entries) if fnmatch.fnmatchcase(path, pattern)]
        return self._json({"paths": paths[:_MAX_RESULTS], "truncated": len(paths) > _MAX_RESULTS})

    async def grep(self, pattern: str) -> str:
        """Search visible UTF-8 text with a bounded regular expression."""

        self._consume()
        try:
            expression = re.compile(pattern)
        except re.error as error:
            raise ValueError("grep pattern is invalid") from error
        matches: list[dict[str, object]] = []
        scanned = 0
        for path, entry in sorted(self._entries.items()):
            payload = await self._payload(entry)
            if b"\0" in payload:
                continue
            scanned += len(payload)
            if scanned > _MAX_SCAN_BYTES:
                break
            text = payload.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if expression.search(line):
                    matches.append({"path": path, "line": line_number, "text": line[:200]})
                    if len(matches) >= _MAX_RESULTS:
                        return self._json({"matches": matches, "truncated": True})
        return self._json({"matches": matches, "truncated": scanned > _MAX_SCAN_BYTES})

    async def read_file(self, path: str, start_line: int, end_line: int) -> str:
        """Read a bounded new-side line range from one visible Snapshot file."""

        self._consume()
        if start_line < 1 or end_line < start_line or end_line - start_line >= _MAX_LINES:
            raise ValueError("line range is invalid")
        payload = await self._payload(self._entry(path))
        if b"\0" in payload:
            raise ValueError("Snapshot file is binary")
        selected = b"".join(payload.splitlines(keepends=True)[start_line - 1 : end_line])
        return self._json(
            {
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "content": selected[:_MAX_READ_BYTES].decode("utf-8", errors="replace"),
                "content_hash": hashlib.sha256(selected).hexdigest(),
                "truncated": len(selected) > _MAX_READ_BYTES,
            }
        )

    async def get_change_map(self) -> str:
        """Return bounded, location-stable evidence for this review's changed hunks."""

        self._consume()
        hunks = [
            {
                "hunk_id": hunk.hunk_id,
                "path": hunk.path,
                "start_line": hunk.start_line,
                "end_line": hunk.end_line,
                "side": hunk.side,
            }
            for hunk in self._snapshot.change_index.hunks[:_MAX_RESULTS]
        ]
        return self._json(
            {
                "snapshot_id": self._snapshot.snapshot_id,
                "target_paths": list(self._snapshot.manifest.target_paths[:_MAX_RESULTS]),
                "hunks": hunks,
                "truncated": len(self._snapshot.change_index.hunks) > _MAX_RESULTS,
            }
        )

    async def get_diff(self, path: str) -> str:
        """Read the bounded base-to-head diff for one changed, visible file."""

        self._consume()
        self._entry(path)
        if path not in {hunk.path for hunk in self._snapshot.change_index.hunks}:
            raise ValueError("path has no changed hunk")
        result = await self._git.run(
            self._snapshot.worktree.root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=3",
            self._snapshot.target.base_oid,
            "--",
            path,
        )
        content = result.stdout[:_MAX_READ_BYTES]
        return self._json(
            {
                "path": path,
                "content": content.decode("utf-8", errors="replace"),
                "content_hash": hashlib.sha256(result.stdout).hexdigest(),
                "truncated": len(result.stdout) > _MAX_READ_BYTES,
            }
        )

    async def read_revision(
        self,
        path: str,
        revision: Literal["base", "head"],
        start_line: int,
        end_line: int,
    ) -> str:
        """Read a bounded base or head version of a visible Snapshot path."""

        self._consume()
        if revision not in {"base", "head"}:
            raise ValueError("revision is invalid")
        if start_line < 1 or end_line < start_line or end_line - start_line >= _MAX_LINES:
            raise ValueError("line range is invalid")
        self._entry(path)
        oid = (
            self._snapshot.target.base_oid
            if revision == "base"
            else self._snapshot.target.head_oid
        )
        result = await self._git.run(
            self._snapshot.worktree.root,
            "show",
            f"{oid}:{path}",
            ok_codes=(0, 128),
        )
        if result.returncode != 0:
            raise ValueError("path is unavailable in revision")
        selected = b"".join(result.stdout.splitlines(keepends=True)[start_line - 1 : end_line])
        return self._json(
            {
                "path": path,
                "revision": revision,
                "start_line": start_line,
                "end_line": end_line,
                "content": selected[:_MAX_READ_BYTES].decode("utf-8", errors="replace"),
                "content_hash": hashlib.sha256(selected).hexdigest(),
                "truncated": len(selected) > _MAX_READ_BYTES,
            }
        )

    def as_agent_tools(self) -> list[Tool]:
        """Expose the fixed read-only contract through the Agents SDK."""

        @function_tool(name_override="explore")
        async def explore_tool(path: str = "") -> str:
            """List visible Snapshot files below a relative directory."""

            return await self.explore(path)

        @function_tool(name_override="glob")
        async def glob_tool(pattern: str) -> str:
            """Find visible Snapshot paths matching a POSIX glob pattern."""

            return await self.glob(pattern)

        @function_tool(name_override="grep")
        async def grep_tool(pattern: str) -> str:
            """Search visible Snapshot text with a regular expression."""

            return await self.grep(pattern)

        @function_tool(name_override="read_file")
        async def read_file_tool(path: str, start_line: int, end_line: int) -> str:
            """Read a bounded line range from a visible Snapshot file."""

            return await self.read_file(path, start_line, end_line)

        @function_tool(name_override="get_change_map")
        async def get_change_map_tool() -> str:
            """Return changed paths and stable changed-hunk locations."""

            return await self.get_change_map()

        @function_tool(name_override="get_diff")
        async def get_diff_tool(path: str) -> str:
            """Read the base-to-head diff for a changed visible file."""

            return await self.get_diff(path)

        @function_tool(name_override="read_revision")
        async def read_revision_tool(
            path: str, revision: Literal["base", "head"], start_line: int, end_line: int
        ) -> str:
            """Read a bounded base or head revision of a visible Snapshot file."""

            return await self.read_revision(path, revision, start_line, end_line)

        return [
            explore_tool,
            glob_tool,
            grep_tool,
            read_file_tool,
            get_change_map_tool,
            get_diff_tool,
            read_revision_tool,
        ]

    def _consume(self) -> None:
        if self._remaining_calls <= 0:
            raise ValueError("tool call budget exceeded")
        self._remaining_calls -= 1

    def _entry(self, path: str) -> SnapshotEntry:
        if not self._is_normalized_relative(path) or path not in self._entries:
            raise ValueError("Snapshot context path is not visible")
        return self._entries[path]

    async def _payload(self, entry: SnapshotEntry) -> bytes:
        if entry.kind == "deleted":
            return b""
        absolute = self._snapshot.worktree.root / entry.path
        resolved = absolute.resolve()
        if not resolved.is_relative_to(self._snapshot.worktree.root):
            raise ValueError("Snapshot context path escapes its worktree")
        payload = await asyncio.to_thread(absolute.read_bytes)
        if hashlib.sha256(payload).hexdigest() != entry.content_hash:
            raise ValueError("Snapshot context content changed")
        return payload

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
        normalized = path[:-1] if path.endswith("/") else path
        if not cls._is_normalized_relative(normalized):
            raise ValueError("directory path is invalid")
        return f"{normalized}/"

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

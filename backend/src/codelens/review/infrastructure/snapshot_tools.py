"""Read-only, bounded tools over a single frozen review Snapshot.

The model never receives a worktree path. Every operation is constrained to a
manifest entry and validates its content hash before returning repository text.
"""

import asyncio
import difflib
import fnmatch
import hashlib
import json
import os
import re
from pathlib import PurePosixPath
from typing import Literal

from agents import Tool, function_tool

from codelens.review.application.review_scope import build_review_files
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

    def __init__(
        self,
        snapshot: ReviewSnapshot,
        git: GitCli,
        *,
        max_tool_calls: int | None,
    ) -> None:
        if max_tool_calls is not None and max_tool_calls <= 0:
            raise ValueError("tool call budget must be positive when configured")
        self._snapshot = snapshot
        self._git = git
        self._remaining_calls = max_tool_calls
        self._entries = {
            entry.path: entry
            for entry in snapshot.manifest.entries
            if entry.origin in {"target", "context"}
        }
        instruction_paths = set(snapshot.manifest.instruction_paths)
        self._instruction_entries = {
            entry.path: entry
            for entry in snapshot.manifest.entries
            if entry.origin == "instruction" and entry.path in instruction_paths
        }
        self._instruction_loaded_paths: set[str] = set()
        self._emitted_instruction_paths: set[str] = set()
        review_files = build_review_files(
            snapshot,
            max_files=max(1, len(snapshot.change_index.files)),
            max_ranges=max(1, len(snapshot.change_index.hunks)),
        )
        self._review_file_paths = tuple(item.path for item in review_files)
        self._review_files_by_path = {item.path: item for item in review_files}

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
        selected = await self._selected_file_lines(path, start_line, end_line)
        raw_content = selected[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
        content = self._add_line_prefixes(raw_content, start_line)
        return self._json(
            {
                "path": path,
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
    ) -> tuple[str, bool]:
        """Derive a bounded excerpt identity for backend Finding resolution only."""

        selected = await self._selected_file_lines(path, start_line, end_line)
        return hashlib.sha256(selected).hexdigest(), len(selected) > _MAX_READ_BYTES

    async def initial_instruction_context(self) -> str:
        """Prefetch root rules and list non-root rule paths without consuming a tool call."""

        applicable_paths = {
            rule_path
            for target_path in self._review_file_paths
            for rule_path in self._instruction_entries
            if self._instruction_order(target_path, rule_path) is not None
        }
        root_entries = tuple(
            sorted(
                (
                    entry
                    for entry in self._instruction_entries.values()
                    if entry.path in applicable_paths
                    if PurePosixPath(entry.path).parent == PurePosixPath(".")
                    and entry.path.casefold() in {"agents.md", "review.md"}
                ),
                key=lambda entry: (0 if entry.path.casefold() == "agents.md" else 1, entry.path),
            )
        )
        root_instructions: list[dict[str, str]] = []
        for entry in root_entries:
            payload = await self._payload(entry)
            if b"\0" in payload:
                raise ValueError("repository instruction is binary")
            root_instructions.append(
                {"path": entry.path, "content": payload.decode("utf-8", errors="strict")}
            )
        self._emitted_instruction_paths.update(entry.path for entry in root_entries)
        root_paths = {entry.path for entry in root_entries}
        return self._json(
            {
                "root_instructions": root_instructions,
                "available_instruction_paths": sorted(
                    path for path in applicable_paths if path not in root_paths
                ),
            }
        )

    async def instruction_loader(self, path: str) -> str:
        """Load the ordered repository rules for one complete Review target path."""

        self._consume()
        if (
            not self._is_normalized_relative(path)
            or path not in self._review_file_paths
        ):
            raise ValueError(
                "instruction_loader requires a complete repository-relative target path"
            )
        applicable = sorted(
            (
                (order, entry)
                for rule_path, entry in self._instruction_entries.items()
                if (order := self._instruction_order(path, rule_path)) is not None
            ),
            key=lambda item: item[0],
        )
        rule_paths = [entry.path for _, entry in applicable]
        new_entries = [
            entry for _, entry in applicable if entry.path not in self._emitted_instruction_paths
        ]
        reused_instruction_paths = [
            entry.path
            for _, entry in applicable
            if entry.path in self._emitted_instruction_paths
        ]
        new_instructions: list[dict[str, str]] = []
        for entry in new_entries:
            payload = await self._payload(entry)
            if b"\0" in payload:
                raise ValueError("repository instruction is binary")
            new_instructions.append(
                {
                    "path": entry.path,
                    "content": payload.decode("utf-8", errors="strict"),
                }
            )
        self._emitted_instruction_paths.update(entry.path for entry in new_entries)
        self._instruction_loaded_paths.add(path)
        return self._json(
            {
                "path": path,
                "rule_paths": rule_paths,
                "new_instructions": new_instructions,
                "reused_instruction_paths": reused_instruction_paths,
            }
        )

    def instructions_loaded_for(self, path: str) -> bool:
        """Return whether repository rules were loaded successfully for one target."""

        return path in self._instruction_loaded_paths

    @property
    def unloaded_instruction_paths(self) -> tuple[str, ...]:
        """Return Review targets whose repository rules have not been loaded."""

        return tuple(
            path
            for path in self._review_file_paths
            if path not in self._instruction_loaded_paths
        )

    async def get_diff(self, path: str) -> str:
        """Read the bounded base-to-head diff for one changed, visible file."""

        self._consume()
        entry = self._entry(path)
        review_file = self._review_files_by_path.get(path)
        if review_file is None:
            raise ValueError("path is not a Review file")
        diff_paths = (
            (review_file.old_path, path)
            if review_file.old_path is not None
            else (path,)
        )
        result = await self._git.run(
            self._snapshot.worktree.root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=3",
            self._snapshot.target.base_oid,
            "--",
            *diff_paths,
        )
        output = result.stdout
        if not output and review_file.change_type == "added":
            output = await self._added_file_diff(entry)
        content = output[:_MAX_READ_BYTES]
        return self._json(
            {
                "path": path,
                "content": content.decode("utf-8", errors="replace"),
                "truncated": len(output) > _MAX_READ_BYTES,
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
        review_file = self._review_files_by_path.get(path)
        if review_file is not None and (
            (review_file.change_type == "added" and revision == "base")
            or (review_file.change_type == "deleted" and revision == "head")
        ):
            raise ValueError("path is unavailable in revision")
        oid = (
            self._snapshot.target.base_oid if revision == "base" else self._snapshot.target.head_oid
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
        raw_content = selected[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
        content = self._add_line_prefixes(raw_content, start_line)
        return self._json(
            {
                "path": path,
                "revision": revision,
                "start_line": start_line,
                "end_line": end_line,
                "content": content,
                "truncated": len(selected) > _MAX_READ_BYTES,
            }
        )

    def as_agent_tools(self, descriptions: dict[str, str]) -> list[Tool]:
        """Expose the fixed read-only contract using startup-loaded descriptions."""

        @function_tool(
            name_override="explore",
            description_override=descriptions["explore"],
        )
        async def explore_tool(path: str = "") -> str:
            """List visible Snapshot files below a relative directory."""

            return await self.explore(path)

        @function_tool(
            name_override="glob",
            description_override=descriptions["glob"],
        )
        async def glob_tool(pattern: str) -> str:
            """Find visible Snapshot paths matching a POSIX glob pattern."""

            return await self.glob(pattern)

        @function_tool(
            name_override="grep",
            description_override=descriptions["grep"],
        )
        async def grep_tool(pattern: str) -> str:
            """Search visible Snapshot text with a regular expression."""

            return await self.grep(pattern)

        @function_tool(
            name_override="read_file",
            description_override=descriptions["read_file"],
        )
        async def read_file_tool(path: str, start_line: int, end_line: int) -> str:
            """Read a bounded line range from a visible Snapshot file."""

            return await self.read_file(path, start_line, end_line)

        @function_tool(
            name_override="instruction_loader",
            description_override=descriptions["instruction_loader"],
        )
        async def instruction_loader_tool(path: str) -> str:
            """Load applicable repository rules for a complete target path."""

            return await self.instruction_loader(path)

        @function_tool(
            name_override="get_diff",
            description_override=descriptions["get_diff"],
        )
        async def get_diff_tool(path: str) -> str:
            """Read the base-to-head diff for a changed visible file."""

            return await self.get_diff(path)

        @function_tool(
            name_override="read_revision",
            description_override=descriptions["read_revision"],
        )
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
            instruction_loader_tool,
            get_diff_tool,
            read_revision_tool,
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
        if entry.kind == "deleted":
            return b""
        absolute = self._snapshot.worktree.root / entry.path
        if entry.kind == "symlink":
            target = await asyncio.to_thread(os.readlink, absolute)
            payload = target.encode("utf-8")
        else:
            resolved = absolute.resolve()
            if not resolved.is_relative_to(self._snapshot.worktree.root):
                raise ValueError("Snapshot context path escapes its worktree")
            payload = await asyncio.to_thread(absolute.read_bytes)
        if hashlib.sha256(payload).hexdigest() != entry.content_hash:
            raise ValueError("Snapshot context content changed")
        return payload

    async def _added_file_diff(self, entry: SnapshotEntry) -> bytes:
        payload = await self._payload(entry)
        mode = "120000" if entry.kind == "symlink" else f"100{entry.mode:o}"
        header = f"diff --git a/{entry.path} b/{entry.path}\nnew file mode {mode}\n"
        if b"\0" in payload:
            return (
                header + f"Binary files /dev/null and b/{entry.path} differ\n"
            ).encode("utf-8")
        lines = payload.decode("utf-8", errors="replace").splitlines(keepends=True)
        diff = difflib.unified_diff(
            (),
            lines,
            fromfile="/dev/null",
            tofile=f"b/{entry.path}",
            n=3,
        )
        return (header + "".join(diff)).encode("utf-8")

    async def _selected_file_lines(
        self,
        path: str,
        start_line: int,
        end_line: int,
    ) -> bytes:
        if start_line < 1 or end_line < start_line or end_line - start_line >= _MAX_LINES:
            raise ValueError("line range is invalid")
        payload = await self._payload(self._entry(path))
        if b"\0" in payload:
            raise ValueError("Snapshot file is binary")
        return b"".join(payload.splitlines(keepends=True)[start_line - 1 : end_line])

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

    @staticmethod
    def _instruction_order(target_path: str, rule_path: str) -> tuple[int, int, str] | None:
        target = PurePosixPath(target_path)
        rule = PurePosixPath(rule_path)
        rule_name = rule.name.casefold()
        if (
            rule.parent == target.parent
            and rule_name == f"{target.name}.review.md".casefold()
        ):
            return (len(rule.parent.parts), 2, rule_path)
        kind_order = {"agents.md": 0, "review.md": 1}.get(rule_name)
        if kind_order is None or not target.parent.is_relative_to(rule.parent):
            return None
        return (len(rule.parent.parts), kind_order, rule_path)

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

    @staticmethod
    def _add_line_prefixes(content: str, start_line: int) -> str:
        """Add line number prefixes to content in format: 'linenum|content'."""
        lines = content.split("\n")
        prefixed = [f"{start_line + i}|{line}" for i, line in enumerate(lines) if line]
        return "\n".join(prefixed)

    async def read_full_file(self, path: str) -> str:
        """Read entire new-side file content for line resolution fallback.

        Unlike read_file, this has no line range limits and is not exposed as an agent tool.
        Used internally by the line resolver when hunk matching fails.
        """
        entry = self._entry(path)
        payload = await self._payload(entry)
        if b"\0" in payload:
            raise ValueError("Snapshot file is binary")
        return payload.decode("utf-8", errors="replace")

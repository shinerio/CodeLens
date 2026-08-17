"""Read-only, bounded tools over a single frozen review Snapshot.

The model never receives a worktree path. Every operation is constrained to a
manifest entry and validates its content hash before returning repository text.
"""

import asyncio
import difflib
import hashlib
import json
import multiprocessing
import os
import re
import stat
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast

from agents import Tool, function_tool
from pydantic import BaseModel, ConfigDict, Field

from codelens.review.application.review_scope import ReviewFileInput, build_review_files
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.domain.tool_results import (
    JsonValue,
    ToolDiagnostic,
    ToolResult,
    ToolResultStatus,
)
from codelens.review.infrastructure.model_paths import (
    AmbiguousRecursiveGlobError,
    InvalidModelGlobError,
    InvalidModelPathError,
    ModelGlob,
    ModelPath,
    match_model_glob,
    normalize_model_path,
    parse_model_glob,
)
from codelens.review.infrastructure.tool_contract import reject_unknown_arguments
from codelens.workspace.domain.models import ReviewSnapshot, SnapshotEntry
from codelens.workspace.infrastructure.git_cli import GitCli

_REGEX_WORKER_STARTUP_TIMEOUT_SECONDS = 15.0
_REGEX_WORKER_READY = "regex_worker_ready"
_FileVersion = Literal["current", "base", "head"]
_ModelLine = Annotated[int, Field(ge=1)]


class ModelLineRange(BaseModel):
    """Validate the strict nullable read_file v2 line range object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_line: _ModelLine
    end_line: _ModelLine


def _regex_search_worker(
    sender: Connection,
    pattern: str,
    lines: tuple[tuple[str, int, str], ...],
    max_results: int,
) -> None:
    """Run untrusted regular-expression matching in a terminable child process."""

    try:
        sender.send(_REGEX_WORKER_READY)
        expression = re.compile(pattern)
        matches: list[dict[str, object]] = []
        match_count = 0
        matched_paths: set[str] = set()
        for path, line_number, line in lines:
            match = expression.search(line)
            if match is not None:
                match_count += 1
                matched_paths.add(path)
                window_start = max(0, min(match.start() - 100, len(line) - 200))
                if len(matches) < max_results:
                    matches.append(
                        {
                            "path": path,
                            "line_number": line_number,
                            "line": line[window_start : window_start + 200],
                        }
                    )
        sender.send((matches, match_count, len(matched_paths)))
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
    lines: tuple[tuple[str, int, str], ...],
    timeout_seconds: float,
    max_results: int,
) -> tuple[list[dict[str, object]], int, int]:
    """Bound worker startup separately, then terminate regex evaluation at its deadline."""

    process_context = multiprocessing.get_context("spawn")
    receiver, sender = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_regex_search_worker,
        args=(sender, pattern, lines, max_results),
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
            or len(message) != 3
            or not isinstance(message[0], list)
            or isinstance(message[1], bool)
            or not isinstance(message[1], int)
            or isinstance(message[2], bool)
            or not isinstance(message[2], int)
        ):
            raise ValueError("grep worker returned an invalid result")
        return message[0], message[1], message[2]
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
        tool_limits: ToolLimits | None = None,
    ) -> None:
        if max_tool_calls is not None and max_tool_calls <= 0:
            raise ValueError("tool call budget must be positive when configured")
        self._snapshot = snapshot
        self._git = git
        self._root = snapshot.worktree.root.resolve()
        self._remaining_calls = max_tool_calls
        self._limits = tool_limits if tool_limits is not None else ToolLimits()
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
        self._reviewed_paths: set[str] = set()
        # Cache for file payloads to avoid repeated disk reads within the same agent run
        self._file_payload_cache: dict[tuple[str, str], bytes] = {}

    async def find_files(self, path: str = "", pattern: str = "**") -> str:
        """Find visible files under one normalized directory using the shared v2 Glob."""

        self._consume()
        try:
            parsed_pattern = self._model_glob(pattern)
            normalized_path = normalize_model_path(path, visible_paths=tuple(self._entries))
        except AmbiguousRecursiveGlobError as error:
            return ToolResult(
                "find_files",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "ambiguous_recursive_glob",
                        "Recursive ** must be a complete path segment.",
                        True,
                        "pattern",
                        {"path": path, "pattern": error.suggested_pattern},
                    ),
                ),
            ).to_json()
        except InvalidModelPathError:
            return ToolResult(
                "find_files",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "invalid_path",
                        "The requested path is not a safe Snapshot-relative path.",
                        True,
                        "path",
                    ),
                ),
            ).to_json()
        except InvalidModelGlobError:
            return ToolResult(
                "find_files",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "invalid_glob_pattern",
                        "The requested Glob pattern is invalid.",
                        True,
                        "pattern",
                    ),
                ),
            ).to_json()
        if normalized_path.scope_type == "file":
            file_path = PurePosixPath(normalized_path.normalized_path)
            parent = "" if str(file_path.parent) == "." else str(file_path.parent)
            return ToolResult(
                "find_files",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "path_is_not_directory",
                        "find_files path must identify a directory scope.",
                        True,
                        "path",
                        {"path": parent, "pattern": file_path.name},
                    ),
                ),
            ).to_json()
        prefix = self._directory_prefix(normalized_path.normalized_path)
        visible_paths = [
            candidate
            for candidate in sorted(self._entries)
            if candidate.startswith(prefix)
        ]
        matched_paths = [
            candidate
            for candidate in visible_paths
            if match_model_glob(candidate[len(prefix) :], parsed_pattern)
        ]
        returned_paths = matched_paths[: self._limits.max_results]
        is_truncated = len(matched_paths) > len(returned_paths)
        diagnostics: tuple[ToolDiagnostic, ...] = ()
        status = ToolResultStatus.PARTIAL if is_truncated else ToolResultStatus.SUCCESS
        if is_truncated:
            diagnostics = (
                ToolDiagnostic(
                    "result_limit_reached",
                    "More files matched than the configured result limit.",
                    True,
                ),
            )
        elif not visible_paths:
            diagnostics = (
                ToolDiagnostic(
                    "empty_directory_scope",
                    "The directory scope has no visible files.",
                    True,
                    "path",
                ),
            )
        elif not matched_paths:
            diagnostics = (
                ToolDiagnostic(
                    "no_files_match_pattern",
                    "Visible files exist, but none match the requested Glob.",
                    True,
                    "pattern",
                    {"path": normalized_path.normalized_path, "pattern": "*"},
                ),
            )
        return ToolResult(
            "find_files",
            status,
            {
                "requested_path": normalized_path.requested_path,
                "normalized_path": normalized_path.normalized_path,
                "scope_type": normalized_path.scope_type,
                "requested_pattern": parsed_pattern.requested_pattern,
                "effective_pattern": parsed_pattern.effective_pattern,
                "pattern_scope": parsed_pattern.pattern_scope,
                "visible_file_count": len(visible_paths),
                "matched_count": len(matched_paths),
                "returned_count": len(returned_paths),
                "paths": cast(JsonValue, returned_paths),
                "truncated": is_truncated,
            },
            diagnostics,
        ).to_json()

    async def grep(
        self,
        pattern: str,
        mode: Literal["literal", "regex"] = "literal",
        path: str = "",
        file_pattern: str = "*",
    ) -> str:
        """Search one visible path scope with literal or isolated regex matching."""

        self._consume()
        if not pattern or len(pattern) > self._limits.max_pattern_chars:
            return self._grep_rejection(
                "invalid_argument_value",
                "grep pattern must be non-empty and within the configured limit.",
                "pattern",
            )
        if mode not in {"literal", "regex"}:
            return self._grep_rejection(
                "invalid_argument_value",
                "grep mode must be literal or regex.",
                "mode",
            )
        if mode == "regex":
            try:
                re.compile(pattern)
            except re.error:
                return self._grep_rejection(
                    "invalid_regular_expression",
                    "The regular expression is invalid.",
                    "pattern",
                )
        try:
            parsed_file_pattern = self._model_glob(file_pattern)
            normalized_path = normalize_model_path(path, visible_paths=tuple(self._entries))
        except AmbiguousRecursiveGlobError as error:
            return ToolResult(
                "grep",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "ambiguous_recursive_glob",
                        "Recursive ** must be a complete path segment.",
                        True,
                        "file_pattern",
                        {
                            "pattern": pattern,
                            "mode": mode,
                            "path": path,
                            "file_pattern": error.suggested_pattern,
                        },
                    ),
                ),
            ).to_json()
        except InvalidModelPathError:
            return self._grep_rejection(
                "invalid_path",
                "The requested path is not a safe Snapshot-relative path.",
                "path",
            )
        except InvalidModelGlobError:
            return self._grep_rejection(
                "invalid_glob_pattern",
                "The requested file Glob is invalid.",
                "file_pattern",
            )
        entries = self._grep_entries(normalized_path, parsed_file_pattern)
        lines: list[tuple[str, int, str]] = []
        scanned_bytes = 0
        scanned_file_count = 0
        skipped_binary_file_count = 0
        is_scan_limited = False
        for candidate_path, entry in entries:
            payload = await self._payload(entry)
            if b"\0" in payload:
                skipped_binary_file_count += 1
                continue
            scanned_file_count += 1
            text = payload.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                encoded_size = len(line.encode("utf-8")) + 1
                if scanned_bytes + encoded_size > self._limits.max_scan_bytes:
                    is_scan_limited = True
                    break
                lines.append((candidate_path, line_number, line))
                scanned_bytes += encoded_size
            if is_scan_limited:
                break
        if mode == "literal":
            all_matches = []
            for candidate_path, line_number, line in lines:
                match_index = line.find(pattern)
                if match_index < 0:
                    continue
                window_start = max(0, min(match_index - 100, len(line) - 200))
                all_matches.append(
                    {
                        "path": candidate_path,
                        "line_number": line_number,
                        "line": line[window_start : window_start + 200],
                    }
                )
            matches = all_matches[: self._limits.max_results]
            match_count = len(all_matches)
            matched_file_count = len({str(match["path"]) for match in all_matches})
        else:
            try:
                matches, match_count, matched_file_count = await asyncio.to_thread(
                    _search_regular_expression,
                    pattern,
                    tuple(lines),
                    self._limits.regex_timeout_seconds,
                    self._limits.max_results,
                )
            except ValueError as error:
                code = (
                    "regular_expression_timed_out"
                    if "timed out" in str(error)
                    else "regular_expression_failed"
                )
                return ToolResult(
                    "grep",
                    ToolResultStatus.FAILED,
                    {},
                    (ToolDiagnostic(code, "Regular-expression evaluation failed.", False),),
                ).to_json()
        matches = self._bounded_grep_matches(matches)
        is_result_limited = match_count > len(matches)
        is_truncated = is_scan_limited or is_result_limited
        diagnostics: list[ToolDiagnostic] = []
        if is_scan_limited:
            diagnostics.append(
                ToolDiagnostic(
                    "scan_limit_reached",
                    "grep stopped at the configured scan byte limit.",
                    True,
                )
            )
        if is_result_limited:
            diagnostics.append(
                ToolDiagnostic(
                    "result_limit_reached",
                    "grep found more matches than the configured result limit.",
                    True,
                )
            )
        if not entries:
            diagnostics.append(
                ToolDiagnostic(
                    "no_candidate_files",
                    "No visible files match the requested path and file Glob.",
                    True,
                    "file_pattern",
                    {
                        "pattern": pattern,
                        "mode": mode,
                        "path": normalized_path.normalized_path,
                        "file_pattern": "*",
                    },
                )
            )
        elif not is_scan_limited and match_count == 0:
            diagnostics.append(
                ToolDiagnostic(
                    "no_content_matches",
                    "Candidate files were scanned completely, but no content matched.",
                    True,
                    "pattern",
                    {
                        "pattern": pattern,
                        "mode": mode,
                        "path": normalized_path.normalized_path,
                        "file_pattern": file_pattern,
                    },
                )
            )
        return ToolResult(
            "grep",
            ToolResultStatus.PARTIAL if is_truncated else ToolResultStatus.SUCCESS,
            {
                "requested_path": normalized_path.requested_path,
                "normalized_path": normalized_path.normalized_path,
                "scope_type": normalized_path.scope_type,
                "pattern": pattern,
                "mode": mode,
                "file_pattern": file_pattern,
                "pattern_scope": parsed_file_pattern.pattern_scope,
                "candidate_file_count": len(entries),
                "scanned_file_count": scanned_file_count,
                "skipped_binary_file_count": skipped_binary_file_count,
                "scanned_bytes": scanned_bytes,
                "matched_file_count": matched_file_count,
                "match_count": match_count,
                "returned_match_count": len(matches),
                "matches": cast(JsonValue, matches),
                "truncated": is_truncated,
            },
            tuple(diagnostics),
        ).to_json()

    def _grep_entries(
        self,
        path: ModelPath,
        file_pattern: ModelGlob,
    ) -> list[tuple[str, SnapshotEntry]]:
        if path.scope_type == "file":
            return [
                (
                    path.normalized_path,
                    self._entries[path.normalized_path],
                )
            ]
        prefix = self._directory_prefix(path.normalized_path)
        return [
            (candidate, entry)
            for candidate, entry in sorted(self._entries.items())
            if candidate.startswith(prefix)
            and match_model_glob(candidate[len(prefix) :], file_pattern)
        ]

    async def read_file(
        self,
        path: str,
        version: _FileVersion = "current",
        line_range: ModelLineRange | None = None,
    ) -> str:
        """Read a UTF-8-safe page while preserving every physical source line."""

        self._consume()
        if version not in {"current", "base", "head"}:
            return self._read_file_rejection(
                "invalid_argument_value",
                "read_file version must be current, base, or head.",
                "version",
            )
        try:
            normalized_path = normalize_model_path(path, visible_paths=tuple(self._entries))
        except InvalidModelPathError:
            return self._read_file_rejection(
                "invalid_path",
                "The requested path is not a safe Snapshot-relative path.",
                "path",
            )
        if normalized_path.scope_type != "file":
            return self._read_file_rejection(
                "path_is_not_file",
                "read_file path must identify one visible file.",
                "path",
            )
        try:
            payload = await self._file_payload(normalized_path.normalized_path, version)
        except ValueError as error:
            code = (
                "source_file_exceeds_limit"
                if "exceeds" in str(error)
                else "file_version_unavailable"
            )
            return self._read_file_rejection(
                code,
                "The requested file version cannot be read.",
                "path",
            )
        physical_lines = payload.splitlines(keepends=True)
        total_lines = len(physical_lines)
        requested_range: dict[str, int] | None = None
        start_line = 1
        requested_end_line = total_lines
        page_line_count = self._limits.max_lines
        if line_range is not None:
            requested_range = {
                "start_line": line_range.start_line,
                "end_line": line_range.end_line,
            }
            start_line = line_range.start_line
            requested_end_line = line_range.end_line
            page_line_count = min(
                self._limits.max_lines,
                line_range.end_line - line_range.start_line + 1,
            )
            if line_range.end_line < line_range.start_line:
                return self._read_file_rejection(
                    "invalid_line_range",
                    "line_range end_line must not precede start_line.",
                    "line_range",
                )
        if start_line > total_lines and not (total_lines == 0 and start_line == 1):
            return self._read_file_rejection(
                "line_range_out_of_bounds",
                "line_range starts after the end of the file.",
                "line_range",
            )
        final_requested_line = min(
            total_lines,
            requested_end_line,
            start_line + page_line_count - 1,
        )
        selected_lines = physical_lines[start_line - 1 : final_requested_line]
        prefixed_lines: list[str] = []
        returned_bytes = 0
        complete_line_count = 0
        line_content_truncated = False
        for offset, raw_line in enumerate(selected_lines):
            line_number = start_line + offset
            line_content = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
            suffix = "\n" if raw_line.endswith((b"\n", b"\r")) else ""
            rendered = f"{line_number:4d} | {line_content}{suffix}"
            rendered_bytes = rendered.encode("utf-8")
            if returned_bytes + len(rendered_bytes) <= self._limits.max_read_bytes:
                prefixed_lines.append(rendered)
                returned_bytes += len(rendered_bytes)
                complete_line_count += 1
                continue
            if complete_line_count == 0:
                prefix = f"{line_number:4d} | ".encode()
                available = max(0, self._limits.max_read_bytes - len(prefix))
                safe_content = self._utf8_safe_prefix(line_content.encode("utf-8"), available)
                rendered_prefix = prefix + safe_content
                prefixed_lines.append(rendered_prefix.decode("utf-8"))
                returned_bytes = len(rendered_prefix)
                line_content_truncated = True
            break
        actual_end_line = (
            start_line + complete_line_count - 1
            if complete_line_count > 0
            else (start_line if line_content_truncated else max(0, start_line - 1))
        )
        next_start_line = start_line + complete_line_count
        has_more = line_content_truncated or next_start_line <= min(
            total_lines, requested_end_line
        )
        next_line_range: dict[str, int] | None = None
        if has_more and not line_content_truncated:
            next_line_range = {
                "start_line": next_start_line,
                "end_line": min(
                    total_lines,
                    requested_end_line,
                    next_start_line + page_line_count - 1,
                ),
            }
        diagnostics: tuple[ToolDiagnostic, ...] = ()
        if line_content_truncated:
            diagnostics = (
                ToolDiagnostic(
                    "line_exceeds_read_limit",
                    "A single source line exceeds the configured read byte limit.",
                    True,
                    "line_range",
                ),
            )
        elif has_more:
            assert next_line_range is not None
            diagnostics = (
                ToolDiagnostic(
                    "read_page_incomplete",
                    "More complete source lines remain in the requested range.",
                    True,
                    "line_range",
                    {
                        "path": normalized_path.normalized_path,
                        "version": version,
                        "line_range": cast(JsonValue, next_line_range),
                    },
                ),
            )
        status = ToolResultStatus.PARTIAL if has_more else ToolResultStatus.SUCCESS
        if normalized_path.normalized_path in self._review_files_by_path and (
            status is ToolResultStatus.SUCCESS or complete_line_count > 0
        ):
            self._reviewed_paths.add(normalized_path.normalized_path)
        return ToolResult(
            "read_file",
            status,
            {
                "requested_path": normalized_path.requested_path,
                "normalized_path": normalized_path.normalized_path,
                "scope_type": normalized_path.scope_type,
                "version": version,
                "requested_line_range": cast(JsonValue, requested_range),
                "actual_line_range": {
                    "start_line": start_line,
                    "end_line": actual_end_line,
                },
                "total_lines": total_lines,
                "returned_bytes": returned_bytes,
                "content": "".join(prefixed_lines),
                "truncated": has_more,
                "line_content_truncated": line_content_truncated,
                "next_line_range": cast(JsonValue, next_line_range),
            },
            diagnostics,
        ).to_json()

    async def excerpt_identity(
        self,
        path: str,
        start_line: int,
        end_line: int,
        version: Literal["current", "base"] = "current",
    ) -> tuple[str, bool]:
        """Derive a bounded excerpt identity for backend Finding resolution only."""

        selected = await self._selected_file_lines(path, start_line, end_line, version)
        return hashlib.sha256(selected).hexdigest(), len(selected) > self._limits.max_read_bytes

    async def get_diff(self, path: str, cursor: str | None = None) -> str:
        """Read one page containing only complete unified-diff hunks."""

        self._consume()
        try:
            normalized_path = normalize_model_path(
                path, visible_paths=tuple(self._entries)
            )
        except InvalidModelPathError:
            return self._get_diff_rejection(
                "invalid_path",
                "The requested path is not a safe Snapshot-relative path.",
                "path",
            )
        paths: tuple[str, ...]
        if normalized_path.scope_type == "file":
            if normalized_path.normalized_path not in self._review_files_by_path:
                return self._get_diff_rejection(
                    "path_is_not_review_file",
                    "The requested file is visible but is not part of this Review diff.",
                    "path",
                )
            paths = (normalized_path.normalized_path,)
        else:
            prefix = self._directory_prefix(normalized_path.normalized_path)
            paths = tuple(
                candidate
                for candidate in sorted(self._review_files_by_path)
                if candidate.startswith(prefix)
            )
        try:
            file_index, hunk_index = self._decode_diff_cursor(
                normalized_path.normalized_path, cursor
            )
        except ValueError:
            return self._get_diff_rejection(
                "invalid_diff_cursor",
                "The diff cursor does not match this frozen path and Snapshot.",
                "cursor",
            )
        if file_index > len(paths) or (file_index == len(paths) and hunk_index != 0):
            return self._get_diff_rejection(
                "invalid_diff_cursor",
                "The diff cursor position is out of range.",
                "cursor",
            )
        parsed_files: list[tuple[str, str, tuple[str, ...]]] = []
        total_hunk_count = 0
        for candidate_path in paths:
            output = (await self._get_diff_payload(candidate_path)).decode("utf-8")
            header, hunks = self._split_unified_diff(output)
            parsed_files.append((candidate_path, header, hunks))
            total_hunk_count += len(hunks)

        returned_files: list[dict[str, JsonValue]] = []
        returned_hunk_count = 0
        completed_file_count = 0
        content_bytes = 0
        next_file_index = file_index
        next_hunk_index = hunk_index
        oversized_hunk: tuple[str, str] | None = None
        oversized_resume_position: tuple[int, int] | None = None
        while (
            next_file_index < len(parsed_files)
            and len(returned_files) < self._limits.max_results
        ):
            candidate_path, header, hunks = parsed_files[next_file_index]
            current_hunk_index = next_hunk_index
            selected_hunks: list[str] = []
            file_bytes = len(header.encode("utf-8"))
            while current_hunk_index < len(hunks):
                hunk = hunks[current_hunk_index]
                hunk_bytes = len(hunk.encode("utf-8"))
                if hunk_bytes > self._limits.max_read_bytes:
                    oversized_hunk = (candidate_path, hunk)
                    next_index = current_hunk_index + 1
                    oversized_resume_position = (
                        (next_file_index, next_index)
                        if next_index < len(hunks)
                        else (next_file_index + 1, 0)
                    )
                    break
                if content_bytes + file_bytes + hunk_bytes > self._limits.max_read_bytes:
                    break
                selected_hunks.append(hunk)
                file_bytes += hunk_bytes
                current_hunk_index += 1
            is_complete = current_hunk_index == len(hunks)
            if not selected_hunks and hunks and not is_complete:
                break
            if not hunks and content_bytes + file_bytes > self._limits.max_read_bytes:
                break
            review_file = self._review_files_by_path[candidate_path]
            returned_files.append(
                {
                    "path": candidate_path,
                    "change_type": review_file.change_type,
                    "old_path": review_file.old_path,
                    "header": header,
                    "hunks": cast(JsonValue, selected_hunks),
                    "is_complete": is_complete,
                    "next_hunk_index": None if is_complete else current_hunk_index,
                }
            )
            content_bytes += file_bytes
            returned_hunk_count += len(selected_hunks)
            if is_complete:
                completed_file_count += 1
                self._reviewed_paths.add(candidate_path)
                next_file_index += 1
                next_hunk_index = 0
            else:
                next_hunk_index = current_hunk_index
                break

        has_more = next_file_index < len(paths)
        diagnostics: list[ToolDiagnostic] = []
        read_file_suggestions: list[dict[str, JsonValue]] = []
        if oversized_hunk is not None:
            oversized_path, oversized_text = oversized_hunk
            read_file_suggestions = self._diff_read_suggestions(
                oversized_path, oversized_text
            )
            diagnostics.append(
                ToolDiagnostic(
                    "diff_hunk_exceeds_limit",
                    "One complete diff hunk exceeds the configured page byte limit.",
                    True,
                )
            )
        elif has_more:
            diagnostics.append(
                ToolDiagnostic(
                    "diff_page_incomplete",
                    "More complete diff hunks remain after this page.",
                    True,
                    "cursor",
                )
            )
        status = (
            ToolResultStatus.NEEDS_ACTION
            if oversized_hunk is not None and returned_hunk_count == 0
            else (ToolResultStatus.PARTIAL if has_more else ToolResultStatus.SUCCESS)
        )
        if status is ToolResultStatus.NEEDS_ACTION:
            # The oversized hunk remains reachable through read_file_suggestions;
            # advance the signed cursor so later hunks do not become unreachable.
            assert oversized_resume_position is not None
            next_file_index, next_hunk_index = oversized_resume_position
            has_more = next_file_index < len(paths)
        next_cursor = (
            self._encode_diff_cursor(
                normalized_path.normalized_path, next_file_index, next_hunk_index
            )
            if has_more
            else None
        )
        return ToolResult(
            "get_diff",
            status,
            {
                "requested_path": normalized_path.requested_path,
                "normalized_path": normalized_path.normalized_path,
                "scope_type": normalized_path.scope_type,
                "total_file_count": len(paths),
                "returned_file_count": len(returned_files),
                "completed_file_count": completed_file_count,
                "total_hunk_count": total_hunk_count,
                "returned_hunk_count": returned_hunk_count,
                "files": cast(JsonValue, returned_files),
                "has_more": has_more,
                "next_cursor": next_cursor,
                "read_file_suggestions": cast(JsonValue, read_file_suggestions),
            },
            tuple(diagnostics),
        ).to_json()

    async def read_diff_for_resolution(self, path: str) -> str:
        """Read a verified diff internally without recording model-visible evidence."""

        output = await self._get_diff_payload(path)
        return self._json({"path": path, "content": output.decode("utf-8"), "truncated": False})

    async def _get_diff_payload(self, path: str) -> bytes:
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
        base_mode = (
            None
            if review_file.change_type == "added"
            else await self._base_revision_mode(path)
        )
        return self._build_verified_diff(
            entry,
            review_file,
            base_payload,
            current_payload,
            base_mode,
        )

    def _encode_diff_cursor(self, path: str, file_index: int, hunk_index: int) -> str:
        """Encode a human-readable ``file_index:hunk_index`` position token.

        The cursor is a plain position reference into the current request's
        resolved file list. It is not cryptographically signed: the tool is a
        read-only view over a frozen snapshot, so tamper-resistance adds model
        burden (opaque base64 echo) without protecting any mutable state. The
        model can read and self-correct the position, and range is validated on
        decode. Path is intentionally omitted: the description instructs the
        model to reuse ``next_cursor`` only with the same ``path`` argument.
        """
        return f"{file_index}:{hunk_index}"

    def _decode_diff_cursor(self, path: str, cursor: str | None) -> tuple[int, int]:
        if cursor is None:
            return 0, 0
        parts = cursor.split(":")
        if (
            len(parts) != 2
            or not parts[0].isdecimal()
            or not parts[1].isdecimal()
        ):
            raise ValueError("get_diff cursor is invalid")
        file_index = int(parts[0])
        hunk_index = int(parts[1])
        return file_index, hunk_index

    @staticmethod
    def _split_unified_diff(content: str) -> tuple[str, tuple[str, ...]]:
        lines = content.splitlines(keepends=True)
        starts = [index for index, line in enumerate(lines) if line.startswith("@@ ")]
        if not starts:
            return content, ()
        header = "".join(lines[: starts[0]])
        hunks = tuple(
            "".join(lines[start:end])
            for start, end in zip(starts, (*starts[1:], len(lines)), strict=True)
        )
        return header, hunks

    @staticmethod
    def _diff_read_suggestions(path: str, hunk: str) -> list[dict[str, JsonValue]]:
        first_line = hunk.partition("\n")[0]
        match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
            first_line,
        )
        if match is None:
            return []
        old_start, old_count_text, new_start, new_count_text = match.groups()
        suggestions: list[dict[str, JsonValue]] = []
        for version, start_text, count_text in (
            ("base", old_start, old_count_text),
            ("current", new_start, new_count_text),
        ):
            count = 1 if count_text is None else int(count_text)
            if count <= 0:
                continue
            start = int(start_text)
            suggestions.append(
                {
                    "path": path,
                    "version": version,
                    "line_range": cast(
                        JsonValue,
                        {"start_line": start, "end_line": start + count - 1},
                    ),
                }
            )
        return suggestions

    @staticmethod
    def _get_diff_rejection(code: str, message: str, field: str) -> str:
        return ToolResult(
            "get_diff",
            ToolResultStatus.REJECTED,
            {},
            (ToolDiagnostic(code, message, True, field),),
        ).to_json()

    @property
    def review_file_paths(self) -> tuple[str, ...]:
        """Return the stable canonical Review target paths."""

        return tuple(sorted(self._review_files_by_path))

    @property
    def reviewed_paths(self) -> frozenset[str]:
        """Return Review paths successfully exposed through read_file or get_diff."""

        return frozenset(self._reviewed_paths)

    def as_agent_tools(self, descriptions: dict[str, str]) -> list[Tool]:
        """Expose the stable read-only contract using startup-loaded descriptions."""

        ModelPath = Annotated[str, Field(max_length=self._limits.max_path_chars)]
        ModelPattern = Annotated[
            str, Field(min_length=1, max_length=self._limits.max_pattern_chars)
        ]
        ModelCursor = Annotated[
            str | None, Field(default=None, min_length=1, max_length=8192)
        ]

        @function_tool(
            name_override="find_files",
            description_override=descriptions["find_files"],
        )
        async def find_files_tool(path: ModelPath, pattern: ModelPattern) -> str:
            """Find visible files below a directory using a relative POSIX glob pattern."""

            return await self.find_files(path, pattern)

        @function_tool(
            name_override="grep",
            description_override=descriptions["grep"],
        )
        async def grep_tool(
            pattern: ModelPattern,
            mode: Literal["literal", "regex"],
            path: ModelPath,
            file_pattern: ModelPattern,
        ) -> str:
            """Search visible Snapshot text literally or with an isolated regular expression."""

            return await self.grep(pattern, mode, path, file_pattern)

        @function_tool(
            name_override="read_file",
            description_override=descriptions["read_file"],
            strict_mode=False,
        )
        async def read_file_tool(
            path: ModelPath,
            version: _FileVersion = "current",
            start_line: _ModelLine | None = None,
            end_line: _ModelLine | None = None,
        ) -> str:
            """Read a bounded range or bounded whole current, base, or head file."""

            line_range: ModelLineRange | None = None
            if start_line is not None and end_line is not None:
                line_range = ModelLineRange(start_line=start_line, end_line=end_line)
            return await self.read_file(path, version, line_range)

        @function_tool(
            name_override="get_diff",
            description_override=descriptions["get_diff"],
            strict_mode=False,
        )
        async def get_diff_tool(path: ModelPath, cursor: ModelCursor = None) -> str:
            """Read one page of diffs for a changed visible file or directory."""

            return await self.get_diff(path, cursor)

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
        if entry.size_bytes > self._limits.max_source_bytes:
            raise ValueError("Snapshot source file exceeds the tool limit")
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
            metadata = await asyncio.to_thread(absolute.stat)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Snapshot context content changed")
            if metadata.st_size > self._limits.max_source_bytes:
                raise ValueError("Snapshot source file exceeds the tool limit")
            payload = await asyncio.to_thread(
                self._read_bounded_regular_file,
                absolute,
                self._limits.max_source_bytes,
            )
        if hashlib.sha256(payload).hexdigest() != entry.content_hash:
            raise ValueError("Snapshot context content changed")
        return payload

    @staticmethod
    def _read_bounded_regular_file(path: Path, max_source_bytes: int) -> bytes:
        """Read at most one source limit plus a sentinel byte to contain races."""

        with path.open("rb") as source:
            payload = source.read(max_source_bytes + 1)
        if len(payload) > max_source_bytes:
            raise ValueError("Snapshot source file exceeds the tool limit")
        return payload

    @staticmethod
    def _build_verified_diff(
        entry: SnapshotEntry,
        review_file: ReviewFileInput,
        base_payload: bytes,
        current_payload: bytes,
        base_mode: int | None,
    ) -> bytes:
        """Build diff text only from pinned base bytes and hash-verified current bytes."""

        change_type = review_file.change_type
        old_path = review_file.old_path
        path = review_file.path
        base_path = old_path or path
        header = f"diff --git a/{base_path} b/{path}\n"
        metadata = ""
        if change_type == "added":
            current_mode = FilesystemReviewTools._canonical_git_mode(entry)
            metadata = f"new file mode {current_mode:o}\n"
        elif change_type == "deleted":
            if base_mode is None:
                raise ValueError("deleted Review file lacks a frozen base mode")
            metadata = f"deleted file mode {base_mode:o}\n"
        elif change_type == "renamed":
            similarity = "similarity index 100%\n" if base_payload == current_payload else ""
            metadata = f"{similarity}rename from {base_path}\nrename to {path}\n"
        if change_type in {"modified", "renamed"} and base_mode is not None:
            current_mode = FilesystemReviewTools._canonical_git_mode(entry)
            if base_mode != current_mode:
                metadata += f"old mode {base_mode:o}\nnew mode {current_mode:o}\n"

        if b"\0" in base_payload or b"\0" in current_payload:
            before = "/dev/null" if change_type == "added" else f"a/{base_path}"
            after = "/dev/null" if change_type == "deleted" else f"b/{path}"
            return f"{header}{metadata}Binary files {before} and {after} differ\n".encode()

        before_lines = base_payload.decode("utf-8", errors="replace").splitlines(keepends=True)
        after_lines = current_payload.decode("utf-8", errors="replace").splitlines(keepends=True)
        diff_lines = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="/dev/null" if change_type == "added" else f"a/{base_path}",
            tofile="/dev/null" if change_type == "deleted" else f"b/{path}",
            n=3,
        )
        body = "".join(
            line if line.endswith("\n") else f"{line}\n\\ No newline at end of file\n"
            for line in diff_lines
        )
        if not body and not metadata:
            return b""
        return f"{header}{metadata}{body}".encode()

    @staticmethod
    def _canonical_git_mode(entry: SnapshotEntry) -> int:
        """Convert Snapshot permission bits to Git's canonical tree mode."""

        if entry.kind == "symlink":
            return 0o120000
        if entry.kind == "file":
            return 0o100000 | (entry.mode & 0o777)
        raise ValueError("deleted Review file has no current Git mode")

    async def _selected_file_lines(
        self,
        path: str,
        start_line: int,
        end_line: int,
        version: _FileVersion = "current",
    ) -> bytes:
        if (
            start_line < 1
            or end_line < start_line
            or end_line - start_line >= self._limits.max_lines
        ):
            raise ValueError("line range is invalid")
        payload = await self._file_payload(path, version)
        return b"".join(payload.splitlines(keepends=True)[start_line - 1 : end_line])

    async def _file_payload(self, path: str, version: _FileVersion) -> bytes:
        """Read one hash-verified Snapshot version and reject binary content."""

        # Check cache first to avoid repeated disk reads
        cache_key = (path, version)
        if cache_key in self._file_payload_cache:
            return self._file_payload_cache[cache_key]

        entry = self._entry(path)
        if version == "current":
            payload = await self._payload(entry)
        else:
            await self._payload(entry)
            payload = await self._revision_payload(path, version)
        if b"\0" in payload:
            raise ValueError("Snapshot file is binary")

        # Cache the payload for subsequent reads
        self._file_payload_cache[cache_key] = payload
        return payload

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

    async def _base_revision_mode(self, path: str) -> int:
        """Read one file mode from the frozen base tree without trusting the worktree."""

        review_file = self._review_files_by_path.get(path)
        revision_path = (
            review_file.old_path
            if review_file is not None and review_file.old_path is not None
            else path
        )
        result = await self._git.run(
            self._root,
            "ls-tree",
            self._snapshot.target.base_oid,
            "--",
            revision_path,
            ok_codes=(0,),
        )
        record = result.stdout.rstrip(b"\n")
        try:
            metadata, recorded_path = record.split(b"\t", 1)
            mode_text, object_type, _object_id = metadata.split(b" ", 2)
            mode = int(mode_text, 8)
        except (ValueError, TypeError):
            raise ValueError("base revision mode is invalid") from None
        if recorded_path.decode("utf-8", errors="strict") != revision_path:
            raise ValueError("base revision mode path does not match")
        if object_type not in {b"blob"} or mode not in {0o100644, 0o100755, 0o120000}:
            raise ValueError("base revision mode is unsupported")
        return mode

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

    def _directory_prefix(self, path: str) -> str:
        if not path:
            return ""
        if len(path) > self._limits.max_path_chars:
            raise ValueError("directory path is invalid")
        normalized = path[:-1] if path.endswith("/") else path
        if not self._is_normalized_relative(normalized):
            raise ValueError("directory path is invalid")
        return f"{normalized}/"

    def _model_glob(self, pattern: str) -> ModelGlob:
        if len(pattern) > self._limits.max_pattern_chars:
            raise InvalidModelGlobError("file pattern exceeds the configured limit")
        return parse_model_glob(pattern)

    @staticmethod
    def _grep_rejection(code: str, message: str, field: str) -> str:
        return ToolResult(
            "grep",
            ToolResultStatus.REJECTED,
            {},
            (ToolDiagnostic(code, message, True, field),),
        ).to_json()

    def _bounded_grep_matches(
        self, matches: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        returned: list[dict[str, object]] = []
        returned_bytes = 0
        for match in matches:
            encoded_bytes = len(
                json.dumps(
                    match,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if returned_bytes + encoded_bytes > self._limits.max_read_bytes:
                break
            returned.append(match)
            returned_bytes += encoded_bytes
        return returned

    @staticmethod
    def _read_file_rejection(code: str, message: str, field: str) -> str:
        return ToolResult(
            "read_file",
            ToolResultStatus.REJECTED,
            {},
            (ToolDiagnostic(code, message, True, field),),
        ).to_json()

    @staticmethod
    def _utf8_safe_prefix(payload: bytes, limit: int) -> bytes:
        candidate = payload[:limit]
        while candidate:
            try:
                candidate.decode("utf-8")
            except UnicodeDecodeError:
                candidate = candidate[:-1]
            else:
                return candidate
        return b""

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

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
            current_payload if version == "current" else await self._revision_payload(path, "base")
        )
        if b"\0" in payload:
            raise ValueError("Snapshot file is binary")
        return payload.decode("utf-8", errors="replace")

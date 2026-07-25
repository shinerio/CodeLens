"""Deterministic line number resolver using sliding window text matching.

Resolves model-quoted code snippets (existing_code) to accurate line ranges
by matching against unified diff hunks or full file content. This avoids
relying on the model to count line numbers, which is error-prone.

Portions are adapted from Alibaba Open Code Review's resolver implementation:
https://github.com/alibaba/open-code-review/blob/c9b145635c6b6343b108941c2a627ac636836c6b/internal/diff/resolver.go

Copyright 2026 Alibaba. Licensed under the Apache License, Version 2.0.
CodeLens rewrote the implementation in Python and changed its integration and
validation behavior for immutable ReviewSnapshot inputs. See LICENSE and NOTICE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class _HunkLineType(IntEnum):
    CONTEXT = 0
    ADDED = 1
    DELETED = 2


@dataclass
class _HunkLine:
    """One line within a unified diff hunk."""

    type: _HunkLineType
    content: str


@dataclass
class _Hunk:
    """Parsed unified diff hunk with line-level detail."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[_HunkLine] = field(default_factory=list)


@dataclass(frozen=True)
class _IndexedLine:
    """Line content paired with its absolute 1-based line number."""

    line_number: int
    content: str


def normalize_line(s: str) -> str:
    """Normalize one line for comparison.

    Strips surrounding whitespace, removes a leading diff marker (+/-),
    then strips again. Used both on diff content and model-quoted code
    so that superficial formatting differences do not break matching.
    """
    s = s.strip()
    if s and s[0] in "+-":
        s = s[1:]
    return s.strip()


def split_and_normalize(code: str) -> list[str]:
    """Split code into non-empty normalized lines."""
    result: list[str] = []
    for raw in code.splitlines():
        normalized = normalize_line(raw)
        if normalized:
            result.append(normalized)
    return result


def parse_hunks(diff_text: str) -> list[_Hunk]:
    """Parse unified diff text into structured hunks."""
    hunks: list[_Hunk] = []
    current: _Hunk | None = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("@@ "):
            match = _HUNK_HEADER.match(raw_line)
            if match is None:
                continue
            old_start_s, old_count_s, new_start_s, new_count_s = match.groups()
            current = _Hunk(
                old_start=int(old_start_s),
                old_count=int(old_count_s or "1"),
                new_start=int(new_start_s),
                new_count=int(new_count_s or "1"),
            )
            hunks.append(current)
        elif current is not None:
            if raw_line.startswith("+"):
                current.lines.append(_HunkLine(_HunkLineType.ADDED, raw_line[1:]))
            elif raw_line.startswith("-"):
                current.lines.append(_HunkLine(_HunkLineType.DELETED, raw_line[1:]))
            elif raw_line.startswith(" "):
                current.lines.append(_HunkLine(_HunkLineType.CONTEXT, raw_line[1:]))
            elif raw_line == "":
                # Treat as context line with empty content
                current.lines.append(_HunkLine(_HunkLineType.CONTEXT, ""))
            # Skip metadata lines like "\ No newline at end of file"

    return hunks


def extract_side_lines(hunk: _Hunk, *, new_side: bool) -> list[_IndexedLine]:
    """Extract indexed lines for one side of a hunk.

    new_side=True: context + added lines, numbered by new-file position.
    new_side=False: context + deleted lines, numbered by old-file position.
    """
    result: list[_IndexedLine] = []
    old_line = hunk.old_start
    new_line = hunk.new_start

    for hunk_line in hunk.lines:
        if hunk_line.type == _HunkLineType.CONTEXT:
            line_num = new_line if new_side else old_line
            result.append(_IndexedLine(line_num, normalize_line(hunk_line.content)))
            old_line += 1
            new_line += 1
        elif hunk_line.type == _HunkLineType.ADDED:
            if new_side:
                result.append(_IndexedLine(new_line, normalize_line(hunk_line.content)))
            new_line += 1
        elif hunk_line.type == _HunkLineType.DELETED:
            if not new_side:
                result.append(_IndexedLine(old_line, normalize_line(hunk_line.content)))
            old_line += 1

    return result


def match_consecutive(
    side_lines: list[_IndexedLine],
    target_lines: list[str],
) -> tuple[int, int] | None:
    """Sliding window exact match.

    Returns (start_line, end_line) of the first consecutive match, or None.
    """
    if not target_lines or len(side_lines) < len(target_lines):
        return None

    window = len(target_lines)
    for i in range(len(side_lines) - window + 1):
        if all(side_lines[i + j].content == target_lines[j] for j in range(window)):
            return side_lines[i].line_number, side_lines[i + window - 1].line_number

    return None


def resolve_from_hunk(diff_text: str, existing_code: str) -> tuple[int, int] | None:
    """Tier 1: resolve line range by matching against diff hunks.

    Tries new-side (context + added) first, then falls back to old-side
    (context + deleted). Returns (start_line, end_line) or None.
    """
    hunks = parse_hunks(diff_text)
    if not hunks:
        return None

    target_lines = split_and_normalize(existing_code)
    if not target_lines:
        return None

    # New-side first
    for hunk in hunks:
        new_side = extract_side_lines(hunk, new_side=True)
        result = match_consecutive(new_side, target_lines)
        if result is not None:
            return result

    # Old-side fallback
    for hunk in hunks:
        old_side = extract_side_lines(hunk, new_side=False)
        result = match_consecutive(old_side, target_lines)
        if result is not None:
            return result

    return None


def resolve_from_file_content(
    file_content: str, existing_code: str
) -> tuple[int, int] | None:
    """Tier 2: resolve line range by matching against full file content.

    Blank lines are filtered out so that "consecutive" means adjacent
    non-blank lines, but the returned line numbers are absolute positions
    in the original file.
    """
    target_lines = split_and_normalize(existing_code)
    if not target_lines:
        return None

    indexed_lines: list[_IndexedLine] = []
    for i, raw_line in enumerate(file_content.splitlines(), start=1):
        normalized = normalize_line(raw_line.rstrip("\r"))
        if normalized:
            indexed_lines.append(_IndexedLine(i, normalized))

    return match_consecutive(indexed_lines, target_lines)

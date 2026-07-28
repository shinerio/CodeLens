"""Atomic JSON persistence for configurable tool-level limits."""

import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict, cast

from codelens.review.domain.tool_limits import ToolLimits


class _ToolLimitsPayload(TypedDict):
    max_results: int
    max_read_bytes: int
    max_scan_bytes: int
    max_source_bytes: int
    max_lines: int
    max_path_chars: int
    max_pattern_chars: int
    regex_timeout_seconds: float
    comment_batch_size: int
    reviewed_files_batch: int
    short_text_max: int
    long_text_max: int
    task_summary_max: int


_INT_FIELDS = (
    "max_results",
    "max_read_bytes",
    "max_scan_bytes",
    "max_source_bytes",
    "max_lines",
    "max_path_chars",
    "max_pattern_chars",
    "comment_batch_size",
    "reviewed_files_batch",
    "short_text_max",
    "long_text_max",
    "task_summary_max",
)


class FilesystemToolLimitsStore:
    """Store tool limits in the local CodeLens data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "tool-limits.json"

    def get_tool_limits(self) -> ToolLimits:
        """Load persisted limits, using product defaults before first save."""

        if not self._path.exists():
            return ToolLimits()
        raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("tool limits are invalid")
        payload = cast(dict[object, object], raw)
        kwargs: dict[str, int | float] = {}
        for field in _INT_FIELDS:
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"tool limits field {field} is invalid")
            kwargs[field] = value
        timeout = payload.get("regex_timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("tool limits field regex_timeout_seconds is invalid")
        kwargs["regex_timeout_seconds"] = float(timeout)
        return ToolLimits(**kwargs)

    def save_tool_limits(self, limits: ToolLimits) -> None:
        """Write a complete limits document and atomically replace the old one."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".tool-limits-",
            suffix=".tmp",
        )
        temporary = Path(name)
        payload: _ToolLimitsPayload = {
            "max_results": limits.max_results,
            "max_read_bytes": limits.max_read_bytes,
            "max_scan_bytes": limits.max_scan_bytes,
            "max_source_bytes": limits.max_source_bytes,
            "max_lines": limits.max_lines,
            "max_path_chars": limits.max_path_chars,
            "max_pattern_chars": limits.max_pattern_chars,
            "regex_timeout_seconds": limits.regex_timeout_seconds,
            "comment_batch_size": limits.comment_batch_size,
            "reviewed_files_batch": limits.reviewed_files_batch,
            "short_text_max": limits.short_text_max,
            "long_text_max": limits.long_text_max,
            "task_summary_max": limits.task_summary_max,
        }
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

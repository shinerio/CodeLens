"""Configurable tool-level limits for Review Agent evidence operations."""

from dataclasses import dataclass

DEFAULT_MAX_RESULTS = 200
DEFAULT_MAX_READ_BYTES = 64 * 1024
DEFAULT_MAX_SCAN_BYTES = 1024 * 1024
DEFAULT_MAX_SOURCE_BYTES = 1024 * 1024
DEFAULT_MAX_LINES = 500
DEFAULT_MAX_PATH_CHARS = 1024
DEFAULT_MAX_PATTERN_CHARS = 512
DEFAULT_REGEX_TIMEOUT_SECONDS = 30.0
DEFAULT_COMMENT_BATCH_SIZE = 20
DEFAULT_SHORT_TEXT_MAX = 240
DEFAULT_LONG_TEXT_MAX = 8000
DEFAULT_TASK_SUMMARY_MAX = 8000

MIN_MAX_RESULTS = 1
MAX_MAX_RESULTS = 10_000
MIN_MAX_READ_BYTES = 1
MAX_MAX_READ_BYTES = 10 * 1024 * 1024
MIN_MAX_SCAN_BYTES = 1
MAX_MAX_SCAN_BYTES = 100 * 1024 * 1024
MIN_MAX_SOURCE_BYTES = 1
MAX_MAX_SOURCE_BYTES = 100 * 1024 * 1024
MIN_MAX_LINES = 1
MAX_MAX_LINES = 10_000
MIN_MAX_PATH_CHARS = 1
MAX_MAX_PATH_CHARS = 4096
MIN_MAX_PATTERN_CHARS = 1
MAX_MAX_PATTERN_CHARS = 4096
MIN_REGEX_TIMEOUT_SECONDS = 0.01
MAX_REGEX_TIMEOUT_SECONDS = 300.0
MIN_COMMENT_BATCH_SIZE = 1
MAX_COMMENT_BATCH_SIZE = 100
MIN_SHORT_TEXT_MAX = 1
MAX_SHORT_TEXT_MAX = 2048
MIN_LONG_TEXT_MAX = 1
MAX_LONG_TEXT_MAX = 64_000
MIN_TASK_SUMMARY_MAX = 1
MAX_TASK_SUMMARY_MAX = 64_000


@dataclass(frozen=True)
class ToolLimits:
    """Bound tool operations to prevent resource exhaustion during Agent runs."""

    max_results: int = DEFAULT_MAX_RESULTS
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    max_lines: int = DEFAULT_MAX_LINES
    max_path_chars: int = DEFAULT_MAX_PATH_CHARS
    max_pattern_chars: int = DEFAULT_MAX_PATTERN_CHARS
    regex_timeout_seconds: float = DEFAULT_REGEX_TIMEOUT_SECONDS
    comment_batch_size: int = DEFAULT_COMMENT_BATCH_SIZE
    short_text_max: int = DEFAULT_SHORT_TEXT_MAX
    long_text_max: int = DEFAULT_LONG_TEXT_MAX
    task_summary_max: int = DEFAULT_TASK_SUMMARY_MAX

    def __post_init__(self) -> None:
        for name, value, lo, hi in (
            ("max_results", self.max_results, MIN_MAX_RESULTS, MAX_MAX_RESULTS),
            ("max_read_bytes", self.max_read_bytes, MIN_MAX_READ_BYTES, MAX_MAX_READ_BYTES),
            ("max_scan_bytes", self.max_scan_bytes, MIN_MAX_SCAN_BYTES, MAX_MAX_SCAN_BYTES),
            (
                "max_source_bytes",
                self.max_source_bytes,
                MIN_MAX_SOURCE_BYTES,
                MAX_MAX_SOURCE_BYTES,
            ),
            ("max_lines", self.max_lines, MIN_MAX_LINES, MAX_MAX_LINES),
            ("max_path_chars", self.max_path_chars, MIN_MAX_PATH_CHARS, MAX_MAX_PATH_CHARS),
            (
                "max_pattern_chars",
                self.max_pattern_chars,
                MIN_MAX_PATTERN_CHARS,
                MAX_MAX_PATTERN_CHARS,
            ),
            (
                "regex_timeout_seconds",
                self.regex_timeout_seconds,
                MIN_REGEX_TIMEOUT_SECONDS,
                MAX_REGEX_TIMEOUT_SECONDS,
            ),
            (
                "comment_batch_size",
                self.comment_batch_size,
                MIN_COMMENT_BATCH_SIZE,
                MAX_COMMENT_BATCH_SIZE,
            ),
            ("short_text_max", self.short_text_max, MIN_SHORT_TEXT_MAX, MAX_SHORT_TEXT_MAX),
            ("long_text_max", self.long_text_max, MIN_LONG_TEXT_MAX, MAX_LONG_TEXT_MAX),
            (
                "task_summary_max",
                self.task_summary_max,
                MIN_TASK_SUMMARY_MAX,
                MAX_TASK_SUMMARY_MAX,
            ),
        ):
            if isinstance(value, bool) or value < lo or value > hi:
                raise ValueError(f"{name} must be between {lo} and {hi}")

"""Configurable tool-level limits for Review Agent evidence operations."""

from dataclasses import dataclass

DEFAULT_MAX_RESULTS = 200
DEFAULT_MAX_READ_BYTES = 64 * 1024
DEFAULT_MAX_SCAN_BYTES = 1024 * 1024
DEFAULT_MAX_SOURCE_BYTES = 1024 * 1024
DEFAULT_MAX_FILE_PAYLOAD_CACHE_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_LINES = 1000
DEFAULT_MAX_PATH_CHARS = 1024
DEFAULT_MAX_PATTERN_CHARS = 512
DEFAULT_REGEX_TIMEOUT_SECONDS = 30.0
DEFAULT_COMMENT_BATCH_SIZE = 20
DEFAULT_SHORT_TEXT_MAX = 240
DEFAULT_LONG_TEXT_MAX = 8000
DEFAULT_TASK_SUMMARY_MAX = 8000
DEFAULT_CONTEXT_COMPACTION_ENABLED = True
DEFAULT_CONTEXT_COMPACTION_TRIGGER_TOKENS = 160000
DEFAULT_CONTEXT_COMPACTION_KEEP_RECENT_EVIDENCE_RESULTS = 6
DEFAULT_CONTEXT_COMPACTION_MAX_RETRIES = 3
DEFAULT_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE = 2.0
DEFAULT_CONTEXT_COMPACTION_RETRY_MAX_DELAY = 30.0
DEFAULT_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES = 3

MIN_MAX_RESULTS = 1
MAX_MAX_RESULTS = 10_000
MIN_MAX_READ_BYTES = 1
MAX_MAX_READ_BYTES = 10 * 1024 * 1024
MIN_MAX_SCAN_BYTES = 1
MAX_MAX_SCAN_BYTES = 100 * 1024 * 1024
MIN_MAX_SOURCE_BYTES = 1
MAX_MAX_SOURCE_BYTES = 100 * 1024 * 1024
MIN_MAX_FILE_PAYLOAD_CACHE_BYTES = 1
MAX_MAX_FILE_PAYLOAD_CACHE_BYTES = 1024 * 1024 * 1024
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
MIN_CONTEXT_COMPACTION_TOKENS = 512
MAX_CONTEXT_COMPACTION_TOKENS = 500000
MIN_CONTEXT_COMPACTION_KEEP_RECENT_EVIDENCE_RESULTS = 0
MAX_CONTEXT_COMPACTION_KEEP_RECENT_EVIDENCE_RESULTS = 100
MIN_CONTEXT_COMPACTION_MAX_RETRIES = 0
MAX_CONTEXT_COMPACTION_MAX_RETRIES = 10
MIN_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE = 0.1
MAX_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE = 60.0
MIN_CONTEXT_COMPACTION_RETRY_MAX_DELAY = 1.0
MAX_CONTEXT_COMPACTION_RETRY_MAX_DELAY = 300.0
MIN_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES = 1
MAX_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES = 10


@dataclass(frozen=True)
class ToolLimits:
    """Bound tool operations to prevent resource exhaustion during Agent runs."""

    max_results: int = DEFAULT_MAX_RESULTS
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    max_file_payload_cache_bytes: int = DEFAULT_MAX_FILE_PAYLOAD_CACHE_BYTES
    max_lines: int = DEFAULT_MAX_LINES
    max_path_chars: int = DEFAULT_MAX_PATH_CHARS
    max_pattern_chars: int = DEFAULT_MAX_PATTERN_CHARS
    regex_timeout_seconds: float = DEFAULT_REGEX_TIMEOUT_SECONDS
    comment_batch_size: int = DEFAULT_COMMENT_BATCH_SIZE
    short_text_max: int = DEFAULT_SHORT_TEXT_MAX
    long_text_max: int = DEFAULT_LONG_TEXT_MAX
    task_summary_max: int = DEFAULT_TASK_SUMMARY_MAX
    context_compaction_enabled: bool = DEFAULT_CONTEXT_COMPACTION_ENABLED
    context_compaction_trigger_tokens: int = DEFAULT_CONTEXT_COMPACTION_TRIGGER_TOKENS
    context_compaction_keep_recent_evidence_results: int = (
        DEFAULT_CONTEXT_COMPACTION_KEEP_RECENT_EVIDENCE_RESULTS
    )
    context_compaction_max_retries: int = DEFAULT_CONTEXT_COMPACTION_MAX_RETRIES
    context_compaction_retry_backoff_base: float = (
        DEFAULT_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE
    )
    context_compaction_retry_max_delay: float = (
        DEFAULT_CONTEXT_COMPACTION_RETRY_MAX_DELAY
    )
    context_compaction_max_consecutive_failures: int = (
        DEFAULT_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES
    )

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
            (
                "max_file_payload_cache_bytes",
                self.max_file_payload_cache_bytes,
                MIN_MAX_FILE_PAYLOAD_CACHE_BYTES,
                MAX_MAX_FILE_PAYLOAD_CACHE_BYTES,
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
            (
                "context_compaction_trigger_tokens",
                self.context_compaction_trigger_tokens,
                MIN_CONTEXT_COMPACTION_TOKENS,
                MAX_CONTEXT_COMPACTION_TOKENS,
            ),
            (
                "context_compaction_keep_recent_evidence_results",
                self.context_compaction_keep_recent_evidence_results,
                MIN_CONTEXT_COMPACTION_KEEP_RECENT_EVIDENCE_RESULTS,
                MAX_CONTEXT_COMPACTION_KEEP_RECENT_EVIDENCE_RESULTS,
            ),
            (
                "context_compaction_max_retries",
                self.context_compaction_max_retries,
                MIN_CONTEXT_COMPACTION_MAX_RETRIES,
                MAX_CONTEXT_COMPACTION_MAX_RETRIES,
            ),
            (
                "context_compaction_retry_backoff_base",
                self.context_compaction_retry_backoff_base,
                MIN_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE,
                MAX_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE,
            ),
            (
                "context_compaction_retry_max_delay",
                self.context_compaction_retry_max_delay,
                MIN_CONTEXT_COMPACTION_RETRY_MAX_DELAY,
                MAX_CONTEXT_COMPACTION_RETRY_MAX_DELAY,
            ),
            (
                "context_compaction_max_consecutive_failures",
                self.context_compaction_max_consecutive_failures,
                MIN_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES,
                MAX_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES,
            ),
        ):
            if isinstance(value, bool) or value < lo or value > hi:
                raise ValueError(f"{name} must be between {lo} and {hi}")
        if not isinstance(self.context_compaction_enabled, bool):
            raise ValueError("context_compaction_enabled must be a boolean")

"""Unit tests for the ToolLimits domain model."""

from __future__ import annotations

import pytest

from codelens.review.domain.tool_limits import (
    DEFAULT_COMMENT_BATCH_SIZE,
    DEFAULT_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_CONTEXT_COMPACTION_MAX_RETRIES,
    DEFAULT_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE,
    DEFAULT_CONTEXT_COMPACTION_RETRY_MAX_DELAY,
    DEFAULT_LONG_TEXT_MAX,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_PATH_CHARS,
    DEFAULT_MAX_PATTERN_CHARS,
    DEFAULT_MAX_READ_BYTES,
    DEFAULT_MAX_RESULTS,
    DEFAULT_MAX_SCAN_BYTES,
    DEFAULT_MAX_SOURCE_BYTES,
    DEFAULT_REGEX_TIMEOUT_SECONDS,
    DEFAULT_SHORT_TEXT_MAX,
    DEFAULT_TASK_SUMMARY_MAX,
    MAX_COMMENT_BATCH_SIZE,
    MAX_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES,
    MAX_CONTEXT_COMPACTION_MAX_RETRIES,
    MAX_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE,
    MAX_CONTEXT_COMPACTION_RETRY_MAX_DELAY,
    MAX_LONG_TEXT_MAX,
    MAX_MAX_LINES,
    MAX_MAX_PATH_CHARS,
    MAX_MAX_PATTERN_CHARS,
    MAX_MAX_READ_BYTES,
    MAX_MAX_RESULTS,
    MAX_MAX_SCAN_BYTES,
    MAX_MAX_SOURCE_BYTES,
    MAX_REGEX_TIMEOUT_SECONDS,
    MAX_SHORT_TEXT_MAX,
    MAX_TASK_SUMMARY_MAX,
    MIN_COMMENT_BATCH_SIZE,
    MIN_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES,
    MIN_CONTEXT_COMPACTION_MAX_RETRIES,
    MIN_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE,
    MIN_CONTEXT_COMPACTION_RETRY_MAX_DELAY,
    MIN_LONG_TEXT_MAX,
    MIN_MAX_LINES,
    MIN_MAX_PATH_CHARS,
    MIN_MAX_PATTERN_CHARS,
    MIN_MAX_READ_BYTES,
    MIN_MAX_RESULTS,
    MIN_MAX_SCAN_BYTES,
    MIN_MAX_SOURCE_BYTES,
    MIN_REGEX_TIMEOUT_SECONDS,
    MIN_SHORT_TEXT_MAX,
    MIN_TASK_SUMMARY_MAX,
    ToolLimits,
)


def test_default_tool_limits_are_within_bounds() -> None:
    limits = ToolLimits()
    assert limits.max_results == DEFAULT_MAX_RESULTS
    assert limits.max_read_bytes == DEFAULT_MAX_READ_BYTES
    assert limits.max_scan_bytes == DEFAULT_MAX_SCAN_BYTES
    assert limits.max_source_bytes == DEFAULT_MAX_SOURCE_BYTES
    assert limits.max_lines == DEFAULT_MAX_LINES
    assert limits.max_path_chars == DEFAULT_MAX_PATH_CHARS
    assert limits.max_pattern_chars == DEFAULT_MAX_PATTERN_CHARS
    assert limits.regex_timeout_seconds == DEFAULT_REGEX_TIMEOUT_SECONDS
    assert limits.comment_batch_size == DEFAULT_COMMENT_BATCH_SIZE
    assert limits.short_text_max == DEFAULT_SHORT_TEXT_MAX
    assert limits.long_text_max == DEFAULT_LONG_TEXT_MAX
    assert limits.task_summary_max == DEFAULT_TASK_SUMMARY_MAX
    assert limits.context_compaction_enabled is True
    assert limits.context_compaction_trigger_bytes == 128 * 1024
    assert limits.context_compaction_keep_recent_evidence_results == 6
    assert (
        limits.context_compaction_max_retries
        == DEFAULT_CONTEXT_COMPACTION_MAX_RETRIES
        == 3
    )
    assert (
        limits.context_compaction_retry_backoff_base
        == DEFAULT_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE
        == 2.0
    )
    assert (
        limits.context_compaction_retry_max_delay
        == DEFAULT_CONTEXT_COMPACTION_RETRY_MAX_DELAY
        == 30.0
    )
    assert (
        limits.context_compaction_max_consecutive_failures
        == DEFAULT_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES
        == 3
    )


def test_tool_limits_accept_boundary_values() -> None:
    min_limits = ToolLimits(
        max_results=MIN_MAX_RESULTS,
        max_read_bytes=MIN_MAX_READ_BYTES,
        max_scan_bytes=MIN_MAX_SCAN_BYTES,
        max_source_bytes=MIN_MAX_SOURCE_BYTES,
        max_lines=MIN_MAX_LINES,
        max_path_chars=MIN_MAX_PATH_CHARS,
        max_pattern_chars=MIN_MAX_PATTERN_CHARS,
        regex_timeout_seconds=MIN_REGEX_TIMEOUT_SECONDS,
        comment_batch_size=MIN_COMMENT_BATCH_SIZE,
        short_text_max=MIN_SHORT_TEXT_MAX,
        long_text_max=MIN_LONG_TEXT_MAX,
        task_summary_max=MIN_TASK_SUMMARY_MAX,
        context_compaction_max_retries=MIN_CONTEXT_COMPACTION_MAX_RETRIES,
        context_compaction_retry_backoff_base=MIN_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE,
        context_compaction_retry_max_delay=MIN_CONTEXT_COMPACTION_RETRY_MAX_DELAY,
        context_compaction_max_consecutive_failures=MIN_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES,
    )
    assert min_limits.max_results == MIN_MAX_RESULTS
    assert (
        min_limits.context_compaction_max_retries
        == MIN_CONTEXT_COMPACTION_MAX_RETRIES
    )

    max_limits = ToolLimits(
        max_results=MAX_MAX_RESULTS,
        max_read_bytes=MAX_MAX_READ_BYTES,
        max_scan_bytes=MAX_MAX_SCAN_BYTES,
        max_source_bytes=MAX_MAX_SOURCE_BYTES,
        max_lines=MAX_MAX_LINES,
        max_path_chars=MAX_MAX_PATH_CHARS,
        max_pattern_chars=MAX_MAX_PATTERN_CHARS,
        regex_timeout_seconds=MAX_REGEX_TIMEOUT_SECONDS,
        comment_batch_size=MAX_COMMENT_BATCH_SIZE,
        short_text_max=MAX_SHORT_TEXT_MAX,
        long_text_max=MAX_LONG_TEXT_MAX,
        task_summary_max=MAX_TASK_SUMMARY_MAX,
        context_compaction_max_retries=MAX_CONTEXT_COMPACTION_MAX_RETRIES,
        context_compaction_retry_backoff_base=MAX_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE,
        context_compaction_retry_max_delay=MAX_CONTEXT_COMPACTION_RETRY_MAX_DELAY,
        context_compaction_max_consecutive_failures=MAX_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES,
    )
    assert max_limits.max_results == MAX_MAX_RESULTS
    assert (
        max_limits.context_compaction_max_consecutive_failures
        == MAX_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES
    )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("max_results", MIN_MAX_RESULTS - 1),
        ("max_results", MAX_MAX_RESULTS + 1),
        ("max_read_bytes", MIN_MAX_READ_BYTES - 1),
        ("max_scan_bytes", MAX_MAX_SCAN_BYTES + 1),
        ("max_source_bytes", MIN_MAX_SOURCE_BYTES - 1),
        ("max_lines", MAX_MAX_LINES + 1),
        ("max_path_chars", MIN_MAX_PATH_CHARS - 1),
        ("max_pattern_chars", MAX_MAX_PATTERN_CHARS + 1),
        ("regex_timeout_seconds", MIN_REGEX_TIMEOUT_SECONDS - 0.1),
        ("regex_timeout_seconds", MAX_REGEX_TIMEOUT_SECONDS + 0.1),
        ("comment_batch_size", MIN_COMMENT_BATCH_SIZE - 1),
        ("comment_batch_size", MAX_COMMENT_BATCH_SIZE + 1),
        ("short_text_max", MAX_SHORT_TEXT_MAX + 1),
        ("long_text_max", MIN_LONG_TEXT_MAX - 1),
        ("task_summary_max", MAX_TASK_SUMMARY_MAX + 1),
        ("context_compaction_max_retries", MIN_CONTEXT_COMPACTION_MAX_RETRIES - 1),
        ("context_compaction_max_retries", MAX_CONTEXT_COMPACTION_MAX_RETRIES + 1),
        (
            "context_compaction_retry_backoff_base",
            MIN_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE - 0.01,
        ),
        (
            "context_compaction_retry_backoff_base",
            MAX_CONTEXT_COMPACTION_RETRY_BACKOFF_BASE + 0.1,
        ),
        (
            "context_compaction_retry_max_delay",
            MIN_CONTEXT_COMPACTION_RETRY_MAX_DELAY - 0.1,
        ),
        (
            "context_compaction_retry_max_delay",
            MAX_CONTEXT_COMPACTION_RETRY_MAX_DELAY + 0.1,
        ),
        (
            "context_compaction_max_consecutive_failures",
            MIN_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES - 1,
        ),
        (
            "context_compaction_max_consecutive_failures",
            MAX_CONTEXT_COMPACTION_MAX_CONSECUTIVE_FAILURES + 1,
        ),
    ],
)
def test_tool_limits_reject_out_of_range_values(field: str, invalid_value: int | float) -> None:
    with pytest.raises(ValueError, match=field):
        ToolLimits(**{field: invalid_value})


def test_tool_limits_reject_boolean_values() -> None:
    with pytest.raises(ValueError, match="max_results"):
        ToolLimits(max_results=True)  # type: ignore[arg-type]


def test_context_compaction_retry_fields_reject_boolean_values() -> None:
    """The four numeric retry config fields must reject bool despite bool being an int subclass."""
    for field in (
        "context_compaction_max_retries",
        "context_compaction_retry_backoff_base",
        "context_compaction_retry_max_delay",
        "context_compaction_max_consecutive_failures",
    ):
        with pytest.raises(ValueError, match=field):
            ToolLimits(**{field: True})  # type: ignore[arg-type]


def test_tool_limits_are_frozen() -> None:
    limits = ToolLimits()
    with pytest.raises(AttributeError):
        limits.max_results = 999  # type: ignore[misc]

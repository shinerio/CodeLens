"""Application service for configurable tool-level limits."""

import asyncio

from codelens.review.domain.ports import ToolLimitsStorePort
from codelens.review.domain.tool_limits import ToolLimits


class ToolLimitsService:
    """Validate and persist tool limits outside the event loop."""

    def __init__(self, store: ToolLimitsStorePort) -> None:
        self._store = store

    async def get(self) -> ToolLimits:
        """Return the limits applied when a new Agent run starts."""

        return await asyncio.to_thread(self._store.get_tool_limits)

    async def update(
        self,
        *,
        max_results: int | None = None,
        max_read_bytes: int | None = None,
        max_scan_bytes: int | None = None,
        max_source_bytes: int | None = None,
        max_lines: int | None = None,
        max_path_chars: int | None = None,
        max_pattern_chars: int | None = None,
        regex_timeout_seconds: float | None = None,
        comment_batch_size: int | None = None,
        short_text_max: int | None = None,
        long_text_max: int | None = None,
        task_summary_max: int | None = None,
        context_compaction_enabled: bool | None = None,
        context_compaction_trigger_bytes: int | None = None,
        context_compaction_keep_recent_evidence_results: int | None = None,
        context_compaction_max_retries: int | None = None,
        context_compaction_retry_backoff_base: float | None = None,
        context_compaction_retry_max_delay: float | None = None,
        context_compaction_max_consecutive_failures: int | None = None,
    ) -> ToolLimits:
        """Merge partial updates into the current limits and atomically persist."""

        current = await asyncio.to_thread(self._store.get_tool_limits)
        limits = ToolLimits(
            max_results=max_results if max_results is not None else current.max_results,
            max_read_bytes=max_read_bytes if max_read_bytes is not None else current.max_read_bytes,
            max_scan_bytes=max_scan_bytes if max_scan_bytes is not None else current.max_scan_bytes,
            max_source_bytes=(
                max_source_bytes if max_source_bytes is not None else current.max_source_bytes
            ),
            max_lines=max_lines if max_lines is not None else current.max_lines,
            max_path_chars=max_path_chars if max_path_chars is not None else current.max_path_chars,
            max_pattern_chars=(
                max_pattern_chars if max_pattern_chars is not None else current.max_pattern_chars
            ),
            regex_timeout_seconds=(
                regex_timeout_seconds
                if regex_timeout_seconds is not None
                else current.regex_timeout_seconds
            ),
            comment_batch_size=(
                comment_batch_size if comment_batch_size is not None else current.comment_batch_size
            ),
            short_text_max=short_text_max if short_text_max is not None else current.short_text_max,
            long_text_max=long_text_max if long_text_max is not None else current.long_text_max,
            task_summary_max=(
                task_summary_max if task_summary_max is not None else current.task_summary_max
            ),
            context_compaction_enabled=(
                context_compaction_enabled
                if context_compaction_enabled is not None
                else current.context_compaction_enabled
            ),
            context_compaction_trigger_bytes=(
                context_compaction_trigger_bytes
                if context_compaction_trigger_bytes is not None
                else current.context_compaction_trigger_bytes
            ),
            context_compaction_keep_recent_evidence_results=(
                context_compaction_keep_recent_evidence_results
                if context_compaction_keep_recent_evidence_results is not None
                else current.context_compaction_keep_recent_evidence_results
            ),
            context_compaction_max_retries=(
                context_compaction_max_retries
                if context_compaction_max_retries is not None
                else current.context_compaction_max_retries
            ),
            context_compaction_retry_backoff_base=(
                context_compaction_retry_backoff_base
                if context_compaction_retry_backoff_base is not None
                else current.context_compaction_retry_backoff_base
            ),
            context_compaction_retry_max_delay=(
                context_compaction_retry_max_delay
                if context_compaction_retry_max_delay is not None
                else current.context_compaction_retry_max_delay
            ),
            context_compaction_max_consecutive_failures=(
                context_compaction_max_consecutive_failures
                if context_compaction_max_consecutive_failures is not None
                else current.context_compaction_max_consecutive_failures
            ),
        )
        await asyncio.to_thread(self._store.save_tool_limits, limits)
        return limits

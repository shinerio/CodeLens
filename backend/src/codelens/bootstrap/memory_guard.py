"""Process memory pressure guard with configurable soft limit and active cleanup.

The Worker process is single-process asyncio (see ``bootstrap/unified.py`` and
``worker/singleton.py``), so all concurrent Review tasks share one OS process
memory space. On memory-constrained hosts (e.g. 4G nodes) unbounded growth risks
OOM-killing the process and starves co-located services.

This module provides a lightweight, sync ``check()`` that classifies current RSS
against a configurable limit, and an async ``cleanup_if_needed()`` that runs
registered cleanup callbacks (transcript eviction, stale SSE subscriber sweep,
etc.) plus a final ``gc.collect()``. Cleanup is rate-limited (30s) and callback
failures are isolated so a faulty callback cannot abort the cleanup chain.
"""

import gc
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import psutil

_LOGGER = logging.getLogger("codelens.bootstrap.memory_guard")

_CLEANUP_RATE_LIMIT_SECONDS: float = 30.0


@dataclass(frozen=True)
class MemoryPressureLevel:
    """Snapshot of process memory pressure classification at one instant."""

    rss_bytes: int
    limit_bytes: int
    cleanup_triggered: bool
    reject_new_tasks: bool


CleanupCallback = Callable[[], Awaitable[None]]


class MemoryGuard:
    """Monitor process RSS against a configurable limit and trigger cleanup callbacks.

    The guard is intentionally non-blocking on the hot path: ``check()`` is
    synchronous and reads only ``psutil.Process.memory_info().rss``. The
    expensive cleanup work runs in ``cleanup_if_needed()`` which is awaited by
    the scheduler poll loop and is rate-limited to avoid thrashing.

    Cleanup callbacks are invoked in registration order. A failing callback is
    logged and skipped; subsequent callbacks still run. ``gc.collect()`` always
    runs last to reclaim cyclic references that long-running asyncio tasks tend
    to accumulate (e.g. closed-over transcripts, retired agent run contexts).
    """

    def __init__(
        self,
        *,
        limit_bytes: int,
        cleanup_threshold_ratio: float = 0.85,
        reject_threshold_ratio: float = 0.95,
        cleanup_callbacks: list[CleanupCallback] | None = None,
    ) -> None:
        if limit_bytes < 512 * 1024 * 1024:
            raise ValueError("memory_limit_mb must be at least 512")
        if not 0 < cleanup_threshold_ratio < reject_threshold_ratio <= 1:
            raise ValueError("thresholds must satisfy 0 < cleanup < reject <= 1")
        self._limit_bytes = limit_bytes
        self._cleanup_threshold_ratio = cleanup_threshold_ratio
        self._reject_threshold_ratio = reject_threshold_ratio
        self._cleanup_callbacks: list[CleanupCallback] = list(cleanup_callbacks or [])
        self._process = psutil.Process()
        self._last_cleanup: float = 0.0

    def add_cleanup_callback(self, callback: CleanupCallback) -> None:
        """Register one additional cleanup callback at the end of the chain."""

        self._cleanup_callbacks.append(callback)

    def check(self) -> MemoryPressureLevel:
        """Read current RSS and classify pressure level (lightweight, sync)."""

        rss = self._process.memory_info().rss
        cleanup = rss >= self._limit_bytes * self._cleanup_threshold_ratio
        reject = rss >= self._limit_bytes * self._reject_threshold_ratio
        return MemoryPressureLevel(
            rss_bytes=rss,
            limit_bytes=self._limit_bytes,
            cleanup_triggered=cleanup,
            reject_new_tasks=reject,
        )

    async def cleanup_if_needed(self, pressure: MemoryPressureLevel) -> None:
        """Run cleanup callbacks in order when pressure exceeds threshold; rate-limited.

        Rate-limited to one cleanup pass per 30 seconds to avoid thrashing the
        event loop when RSS hovers near the threshold. ``gc.collect()`` runs last
        to reclaim cyclic references left behind by completed Review tasks.
        """

        if not pressure.cleanup_triggered:
            return
        now = time.monotonic()
        if now - self._last_cleanup < _CLEANUP_RATE_LIMIT_SECONDS:
            return
        self._last_cleanup = now
        _LOGGER.warning(
            "Memory pressure detected, triggering cleanup",
            extra={
                "rss_mb": pressure.rss_bytes // (1024 * 1024),
                "limit_mb": pressure.limit_bytes // (1024 * 1024),
            },
        )
        await self._run_callbacks()

    async def cleanup(self) -> None:
        """Force immediate cleanup regardless of pressure (used on shutdown/recovery)."""

        self._last_cleanup = time.monotonic()
        await self._run_callbacks()

    async def _run_callbacks(self) -> None:
        """Invoke every registered callback; isolate failures; finish with gc.collect."""

        for callback in self._cleanup_callbacks:
            try:
                await callback()
            except Exception:
                _LOGGER.exception("Memory cleanup callback failed")
        gc.collect()

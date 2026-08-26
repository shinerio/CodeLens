"""Unit tests for the process memory pressure guard."""

from unittest.mock import patch

import pytest

from codelens.bootstrap.memory_guard import MemoryGuard, MemoryPressureLevel


def _make_pressure(rss_bytes: int, limit_bytes: int) -> MemoryPressureLevel:
    """Build a pressure level without invoking psutil."""

    cleanup = rss_bytes >= limit_bytes * 0.85
    reject = rss_bytes >= limit_bytes * 0.95
    return MemoryPressureLevel(
        rss_bytes=rss_bytes,
        limit_bytes=limit_bytes,
        cleanup_triggered=cleanup,
        reject_new_tasks=reject,
    )


def test_memory_guard_rejects_limit_below_512mb() -> None:
    with pytest.raises(ValueError, match="at least 512"):
        MemoryGuard(limit_bytes=256 * 1024 * 1024)


def test_memory_guard_rejects_invalid_thresholds() -> None:
    limit = 1024 * 1024 * 1024
    with pytest.raises(ValueError, match="thresholds"):
        MemoryGuard(limit_bytes=limit, cleanup_threshold_ratio=0.9, reject_threshold_ratio=0.8)
    with pytest.raises(ValueError, match="thresholds"):
        MemoryGuard(limit_bytes=limit, cleanup_threshold_ratio=0.0, reject_threshold_ratio=0.9)
    with pytest.raises(ValueError, match="thresholds"):
        MemoryGuard(limit_bytes=limit, cleanup_threshold_ratio=0.9, reject_threshold_ratio=1.5)


def test_memory_guard_check_returns_no_pressure_below_cleanup_threshold() -> None:
    guard = MemoryGuard(limit_bytes=1024 * 1024 * 1024)
    with patch.object(guard._process, "memory_info") as mock_info:
        mock_info.return_value.rss = 500 * 1024 * 1024
        pressure = guard.check()
    assert not pressure.cleanup_triggered
    assert not pressure.reject_new_tasks


def test_memory_guard_check_flags_cleanup_threshold() -> None:
    guard = MemoryGuard(limit_bytes=1024 * 1024 * 1024)
    with patch.object(guard._process, "memory_info") as mock_info:
        mock_info.return_value.rss = int(0.9 * 1024 * 1024 * 1024)
        pressure = guard.check()
    assert pressure.cleanup_triggered
    assert not pressure.reject_new_tasks


def test_memory_guard_check_flags_reject_threshold() -> None:
    guard = MemoryGuard(limit_bytes=1024 * 1024 * 1024)
    with patch.object(guard._process, "memory_info") as mock_info:
        mock_info.return_value.rss = int(0.97 * 1024 * 1024 * 1024)
        pressure = guard.check()
    assert pressure.cleanup_triggered
    assert pressure.reject_new_tasks


async def test_cleanup_if_needed_skips_when_below_threshold() -> None:
    guard = MemoryGuard(limit_bytes=1024 * 1024 * 1024)
    calls: list[str] = []

    async def _callback() -> None:
        calls.append("ran")

    guard.add_cleanup_callback(_callback)
    await guard.cleanup_if_needed(_make_pressure(100 * 1024 * 1024, 1024 * 1024 * 1024))
    assert calls == []


async def test_cleanup_if_needed_runs_callbacks_above_threshold() -> None:
    guard = MemoryGuard(limit_bytes=1024 * 1024 * 1024)
    calls: list[str] = []

    async def _callback() -> None:
        calls.append("ran")

    guard.add_cleanup_callback(_callback)
    await guard.cleanup_if_needed(_make_pressure(int(0.9 * 1024 * 1024 * 1024), 1024 * 1024 * 1024))
    assert calls == ["ran"]


async def test_cleanup_if_needed_is_rate_limited() -> None:
    guard = MemoryGuard(limit_bytes=1024 * 1024 * 1024)
    calls: list[int] = []

    async def _callback() -> None:
        calls.append(1)

    guard.add_cleanup_callback(_callback)
    pressure = _make_pressure(int(0.9 * 1024 * 1024 * 1024), 1024 * 1024 * 1024)
    await guard.cleanup_if_needed(pressure)
    await guard.cleanup_if_needed(pressure)  # immediate second call: rate-limited
    assert calls == [1]


async def test_cleanup_callback_failures_are_isolated() -> None:
    guard = MemoryGuard(limit_bytes=1024 * 1024 * 1024)
    calls: list[str] = []

    async def _failing() -> None:
        raise RuntimeError("boom")

    async def _healthy() -> None:
        calls.append("healthy")

    guard.add_cleanup_callback(_failing)
    guard.add_cleanup_callback(_healthy)
    await guard.cleanup_if_needed(_make_pressure(int(0.9 * 1024 * 1024 * 1024), 1024 * 1024 * 1024))
    assert calls == ["healthy"]

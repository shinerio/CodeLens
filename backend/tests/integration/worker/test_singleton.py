import asyncio
import errno
from pathlib import Path

import pytest

from codelens.worker.singleton import (
    UnixWorkerSingletonAdapter,
    WindowsWorkerSingletonAdapter,
    WorkerAlreadyRunningError,
)


class FakeWindowsLocking:
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.modes: list[int] = []

    def locking(self, _fd: int, mode: int, _count: int) -> None:
        self.modes.append(mode)
        if self.reject and mode == self.LK_NBLCK:
            raise OSError(errno.EACCES, "locked")


async def test_native_lock_rejects_second_worker_and_stale_text_never_blocks(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "worker.lock"
    await asyncio.to_thread(lock_path.write_text, "stale pid=999999\n", encoding="utf-8")
    first = UnixWorkerSingletonAdapter(lock_path)
    second = UnixWorkerSingletonAdapter(lock_path)

    await first.acquire()
    with pytest.raises(WorkerAlreadyRunningError) as caught:
        await second.acquire()
    assert caught.value.code == "worker_already_running"

    await first.release()
    await second.acquire()
    await second.release()


async def test_windows_adapter_uses_nonblocking_byte_lock_and_maps_contention(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "worker.lock"
    locking = FakeWindowsLocking()
    adapter = WindowsWorkerSingletonAdapter(lock_path, locking)

    await adapter.acquire()
    await adapter.release()

    assert locking.modes == [locking.LK_NBLCK, locking.LK_UNLCK]
    rejected = WindowsWorkerSingletonAdapter(lock_path, FakeWindowsLocking(reject=True))
    with pytest.raises(WorkerAlreadyRunningError) as caught:
        await rejected.acquire()
    assert caught.value.code == "worker_already_running"

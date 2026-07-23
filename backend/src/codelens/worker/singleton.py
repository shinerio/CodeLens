"""Platform file-lock adapters for the singleton Worker."""

import asyncio
import errno
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol, cast


class WorkerSingletonPort(Protocol):
    """Hold OS-released exclusive Worker ownership for the complete process lifetime."""

    async def acquire(self) -> None:
        """Acquire ownership or raise the stable already-running error."""

        raise NotImplementedError

    async def release(self) -> None:
        """Release ownership only after all Worker resources have closed."""

        raise NotImplementedError


class WorkerAlreadyRunningError(RuntimeError):
    """Report a stable error when another process owns the Worker kernel lock."""

    code = "worker_already_running"


class UnixWorkerSingletonAdapter:
    """Hold a Unix advisory file lock for the complete Worker lifetime."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path.expanduser().resolve()
        self._file: BinaryIO | None = None

    async def acquire(self) -> None:
        """Acquire non-blocking `flock`; stale diagnostic bytes do not affect ownership."""

        if self._file is not None:
            raise RuntimeError("Worker singleton is already acquired by this adapter")
        self._file = await asyncio.to_thread(self._acquire_sync)

    async def release(self) -> None:
        """Release the kernel lock and descriptor after Worker resources close."""

        file = self._file
        self._file = None
        if file is not None:
            await asyncio.to_thread(self._release_sync, file)

    def _acquire_sync(self) -> BinaryIO:
        import fcntl

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        file = self._lock_path.open("a+b")
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            file.close()
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise WorkerAlreadyRunningError("another Worker owns the singleton lock") from None
            raise
        _write_diagnostic(file)
        return file

    @staticmethod
    def _release_sync(file: BinaryIO) -> None:
        import fcntl

        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()


class WindowsLockingPort(Protocol):
    """Describe the stdlib `msvcrt` constants and byte-range lock calls."""

    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, count: int) -> None: ...


class WindowsWorkerSingletonAdapter:
    """Hold a one-byte Windows CRT lock for the complete Worker lifetime."""

    def __init__(self, lock_path: Path, locking: WindowsLockingPort | None = None) -> None:
        self._lock_path = lock_path.expanduser().resolve()
        if locking is None:
            import msvcrt

            locking = cast(WindowsLockingPort, msvcrt)
        self._locking: WindowsLockingPort = locking
        self._file: BinaryIO | None = None

    async def acquire(self) -> None:
        if self._file is not None:
            raise RuntimeError("Worker singleton is already acquired by this adapter")
        self._file = await asyncio.to_thread(self._acquire_sync)

    async def release(self) -> None:
        file = self._file
        self._file = None
        if file is not None:
            await asyncio.to_thread(self._release_sync, file)

    def _acquire_sync(self) -> BinaryIO:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        file = self._lock_path.open("a+b")
        if file.tell() == 0:
            file.write(b"\0")
            file.flush()
        file.seek(0)
        try:
            self._locking.locking(file.fileno(), self._locking.LK_NBLCK, 1)
        except OSError as error:
            file.close()
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise WorkerAlreadyRunningError("another Worker owns the singleton lock") from None
            raise
        _write_diagnostic(file)
        return file

    def _release_sync(self, file: BinaryIO) -> None:
        try:
            file.seek(0)
            self._locking.locking(file.fileno(), self._locking.LK_UNLCK, 1)
        finally:
            file.close()


def _write_diagnostic(file: BinaryIO) -> None:
    diagnostic = f"pid={os.getpid()} started_at={datetime.now(UTC).isoformat()}\n".encode("ascii")
    file.seek(0)
    file.truncate()
    file.write(diagnostic)
    file.flush()
    os.fsync(file.fileno())


type PlatformWorkerSingleton = UnixWorkerSingletonAdapter | WindowsWorkerSingletonAdapter


def platform_worker_singleton(data_dir: Path) -> PlatformWorkerSingleton:
    """Select the native stdlib kernel-lock adapter for the current platform."""

    lock_path = data_dir.expanduser().resolve() / "worker.lock"
    if sys.platform == "win32":
        return WindowsWorkerSingletonAdapter(lock_path)
    return UnixWorkerSingletonAdapter(lock_path)

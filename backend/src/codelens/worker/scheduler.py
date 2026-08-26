"""Structured-concurrency review scheduler."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from codelens.worker.singleton import WorkerSingletonPort

if TYPE_CHECKING:
    from codelens.bootstrap.memory_guard import MemoryGuard

_LOGGER = logging.getLogger("codelens.worker.scheduler")


def fair_per_review_agent_limit(
    *, configured_limit: int, global_limit: int, max_active_reviews: int
) -> int:
    """Reserve global Agent capacity for a peer task whenever concurrency permits."""

    if min(configured_limit, global_limit, max_active_reviews) < 1:
        raise ValueError("Worker concurrency limits must be positive")
    if max_active_reviews == 1 or global_limit == 1:
        return min(configured_limit, global_limit)
    return min(configured_limit, global_limit - 1)


@dataclass(frozen=True)
class WorkerSemaphores:
    """Share bounded Agent, model, and tool capacity across active reviews."""

    agent: asyncio.Semaphore
    model: asyncio.Semaphore
    tool: asyncio.Semaphore

    @classmethod
    def create(cls, *, agent_limit: int, model_limit: int, tool_limit: int) -> "WorkerSemaphores":
        return cls(
            asyncio.Semaphore(agent_limit),
            asyncio.Semaphore(model_limit),
            asyncio.Semaphore(tool_limit),
        )


class ClaimedJob(Protocol):
    """Expose the stable task identity of one atomically claimed queue job."""

    @property
    def task_id(self) -> str: ...


_JobT = TypeVar("_JobT", bound=ClaimedJob, covariant=True)


class _QueuePort(Protocol[_JobT]):
    async def next_queued(self) -> _JobT | None: ...


class ReviewScheduler:
    """Poll durable jobs and isolate active reviews under one task group."""

    def __init__(
        self,
        *,
        queue: _QueuePort[ClaimedJob],
        execute: Callable[[str], Awaitable[None]],
        singleton: WorkerSingletonPort,
        recover: Callable[[], Awaitable[None]],
        close: Callable[[], Awaitable[None]],
        semaphores: WorkerSemaphores,
        max_active_reviews: int,
        poll_min_seconds: float,
        poll_max_seconds: float,
        record_failure: Callable[[str, Exception], Awaitable[None]] | None = None,
        record_claim: Callable[[str], Awaitable[None]] | None = None,
        memory_guard: "MemoryGuard | None" = None,
        memory_check_interval_seconds: float = 5.0,
    ) -> None:
        self._queue = queue
        self._execute = execute
        self._singleton = singleton
        self._recover = recover
        self._close = close
        self._semaphores = semaphores
        self._max_active_reviews = max_active_reviews
        self._poll_min_seconds = poll_min_seconds
        self._poll_max_seconds = poll_max_seconds
        self._record_failure = record_failure
        self._record_claim = record_claim
        self._memory_guard = memory_guard
        self._memory_check_interval_seconds = memory_check_interval_seconds
        self._last_memory_check: float = 0.0
        self._stop = asyncio.Event()
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        if max_active_reviews < 1:
            raise ValueError("active review limit must be positive")
        if not 0 < poll_min_seconds <= poll_max_seconds:
            raise ValueError("Worker poll bounds are invalid")
        if memory_check_interval_seconds <= 0:
            raise ValueError("memory_check_interval_seconds must be positive")

    @property
    def semaphores(self) -> WorkerSemaphores:
        """Expose the scheduler-owned shared limits for orchestrator composition."""

        return self._semaphores

    def active_task_ids(self) -> set[str]:
        """Return the task IDs currently being executed (snapshot, may race)."""

        return set(self._active_tasks.keys())

    def set_memory_guard(self, guard: "MemoryGuard") -> None:
        """Late-bind a memory guard after construction.

        The guard's cleanup callbacks may need ``active_task_ids()`` which is
        only available once the scheduler exists, so the guard is wired in two
        steps: scheduler constructed first, then guard created with callbacks
        that close over ``scheduler.active_task_ids``, then injected here.
        """

        self._memory_guard = guard

    def stop(self) -> None:
        """Stop claiming new jobs and begin bounded active-task cancellation."""

        self._stop.set()

    def cancel_task(self, task_id: str) -> None:
        """Cancel a running review task by its ID."""

        task = self._active_tasks.get(task_id)
        if task is not None:
            task.cancel()

    def _make_unregister_callback(self, task_id: str) -> Callable[[asyncio.Task[None]], None]:
        """Return a callback that removes a task from the active registry."""

        def unregister(task: asyncio.Task[None]) -> None:
            self._active_tasks.pop(task_id, None)

        return unregister

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Acquire singleton ownership, recover once, and supervise isolated tasks."""

        stop_event = stop or self._stop
        acquired = False
        try:
            await self._singleton.acquire()
            acquired = True
            _LOGGER.info("Worker singleton acquired")
            try:
                await self._recover()
                _LOGGER.info("Worker recovery completed")
            except Exception:
                _LOGGER.exception("Worker recovery failed; continuing with poll loop")
            await self._poll(stop_event)
        finally:
            if acquired:
                try:
                    await self._close()
                finally:
                    await self._singleton.release()

    async def _poll(self, stop: asyncio.Event) -> None:
        active: set[asyncio.Task[None]] = set()
        backoff = self._poll_min_seconds
        async with asyncio.TaskGroup() as tasks:
            while not stop.is_set():
                # Memory pressure check: when RSS approaches the configured limit,
                # run registered cleanup callbacks and (at the reject threshold)
                # pause claiming new tasks so running tasks can drain and release.
                if self._memory_guard is not None:
                    now = time.monotonic()
                    if now - self._last_memory_check >= self._memory_check_interval_seconds:
                        self._last_memory_check = now
                        pressure = self._memory_guard.check()
                        if pressure.cleanup_triggered:
                            await self._memory_guard.cleanup_if_needed(pressure)
                        if pressure.reject_new_tasks:
                            _LOGGER.warning(
                                "Memory limit exceeded, pausing new task claims",
                                extra={
                                    "rss_mb": pressure.rss_bytes // (1024 * 1024),
                                    "limit_mb": pressure.limit_bytes // (1024 * 1024),
                                },
                            )
                            try:
                                await asyncio.wait_for(stop.wait(), timeout=backoff)
                            except TimeoutError:
                                pass
                            backoff = min(self._poll_max_seconds, backoff * 2)
                            continue
                claimed = False
                while len(active) < self._max_active_reviews and not stop.is_set():
                    job = await self._queue.next_queued()
                    if job is None:
                        break
                    if self._record_claim is not None:
                        await self._record_claim(job.task_id)
                    task = tasks.create_task(self._execute_isolated(job.task_id))
                    _LOGGER.info("Review job claimed", extra={"task_id": job.task_id})
                    active.add(task)
                    self._active_tasks[job.task_id] = task
                    task.add_done_callback(active.discard)
                    task.add_done_callback(self._make_unregister_callback(job.task_id))
                    claimed = True
                if claimed:
                    backoff = self._poll_min_seconds
                    await asyncio.sleep(0)
                    continue
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(self._poll_max_seconds, backoff * 2)
            for task in tuple(active):
                task.cancel()

    async def _execute_isolated(self, task_id: str) -> None:
        try:
            await self._execute(task_id)
            _LOGGER.info("Review job completed", extra={"task_id": task_id})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _LOGGER.exception(
                "Review job failed",
                extra={"task_id": task_id, "error_type": type(error).__name__},
            )
            if self._record_failure is not None:
                await self._record_failure(task_id, error)

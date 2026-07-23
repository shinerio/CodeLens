"""Structured-concurrency review scheduler."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from codelens.worker.singleton import WorkerSingletonPort

_LOGGER = logging.getLogger("codelens.worker.scheduler")


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
        self._stop = asyncio.Event()
        if max_active_reviews < 1:
            raise ValueError("active review limit must be positive")
        if not 0 < poll_min_seconds <= poll_max_seconds:
            raise ValueError("Worker poll bounds are invalid")

    @property
    def semaphores(self) -> WorkerSemaphores:
        """Expose the scheduler-owned shared limits for orchestrator composition."""

        return self._semaphores

    def stop(self) -> None:
        """Stop claiming new jobs and begin bounded active-task cancellation."""

        self._stop.set()

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Acquire singleton ownership, recover once, and supervise isolated tasks."""

        stop_event = stop or self._stop
        acquired = False
        try:
            await self._singleton.acquire()
            acquired = True
            _LOGGER.info("Worker singleton acquired")
            await self._recover()
            _LOGGER.info("Worker recovery completed")
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
                    task.add_done_callback(active.discard)
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

"""In-memory event bus for real-time SSE delivery without database polling."""

import asyncio
import logging
import time
from collections import defaultdict

from codelens.review.domain.ports import ReviewEvent

_LOGGER = logging.getLogger("codelens.review.event_bus")


class InMemoryEventBus:
    """Publish events to connected SSE subscribers within one process.

    Events are delivered to per-task subscriber queues after the originating
    database transaction has committed.  The bus is fire-and-forget: if no
    subscribers are listening the event is silently dropped (the durable
    outbox in SQLite remains the source of truth for catch-up replay).

    Subscribers that abandon their queue (e.g. a browser tab that closed
    without triggering the SSE ``finally`` unsubscribe) would otherwise leak
    the queue and any buffered events forever. ``evict_stale_subscribers``
    drops queues that have not received traffic for a configurable idle
    window; the scheduler invokes it from its poll loop.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[ReviewEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_activity: dict[int, float] = {}

    async def publish(self, event: ReviewEvent) -> None:
        """Deliver one event to every active subscriber for its task."""

        async with self._lock:
            queues = list(self._subscribers.get(event.task_id, ()))
        now = time.monotonic()
        for queue in queues:
            try:
                queue.put_nowait(event)
                self._last_activity[id(queue)] = now
            except asyncio.QueueFull:
                _LOGGER.warning(
                    "Dropping event for slow subscriber",
                    extra={"task_id": event.task_id, "event_type": event.event_type},
                )

    async def subscribe(self, task_id: str) -> asyncio.Queue[ReviewEvent]:
        """Register a new subscriber queue for one task."""

        queue: asyncio.Queue[ReviewEvent] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers[task_id].append(queue)
            self._last_activity[id(queue)] = time.monotonic()
        return queue

    async def unsubscribe(self, task_id: str, queue: asyncio.Queue[ReviewEvent]) -> None:
        """Remove a subscriber queue; safe to call more than once."""

        async with self._lock:
            try:
                self._subscribers[task_id].remove(queue)
            except ValueError:
                pass
            self._last_activity.pop(id(queue), None)
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]

    async def evict_stale_subscribers(self, max_idle_seconds: float = 60.0) -> int:
        """Drop queues that have not received events for ``max_idle_seconds``.

        Returns the number of subscriber queues evicted. A queue that has
        never received an event uses its subscribe time as the activity
        baseline, so a freshly subscribed queue is never evicted immediately.
        """

        now = time.monotonic()
        evicted = 0
        async with self._lock:
            for task_id in list(self._subscribers.keys()):
                live: list[asyncio.Queue[ReviewEvent]] = []
                for queue in self._subscribers[task_id]:
                    last = self._last_activity.get(id(queue), now)
                    if now - last > max_idle_seconds:
                        self._last_activity.pop(id(queue), None)
                        evicted += 1
                    else:
                        live.append(queue)
                if live:
                    self._subscribers[task_id] = live
                else:
                    del self._subscribers[task_id]
        return evicted

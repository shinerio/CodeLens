"""In-memory event bus for real-time SSE delivery without database polling."""

import asyncio
import logging
from collections import defaultdict

from codelens.review.domain.ports import ReviewEvent

_LOGGER = logging.getLogger("codelens.review.event_bus")


class InMemoryEventBus:
    """Publish events to connected SSE subscribers within one process.

    Events are delivered to per-task subscriber queues after the originating
    database transaction has committed.  The bus is fire-and-forget: if no
    subscribers are listening the event is silently dropped (the durable
    outbox in SQLite remains the source of truth for catch-up replay).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[ReviewEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, event: ReviewEvent) -> None:
        """Deliver one event to every active subscriber for its task."""

        async with self._lock:
            queues = list(self._subscribers.get(event.task_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(event)
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
        return queue

    async def unsubscribe(self, task_id: str, queue: asyncio.Queue[ReviewEvent]) -> None:
        """Remove a subscriber queue; safe to call more than once."""

        async with self._lock:
            try:
                self._subscribers[task_id].remove(queue)
            except ValueError:
                pass
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]

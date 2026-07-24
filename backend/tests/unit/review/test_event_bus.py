"""Unit tests for the in-memory event bus."""

from codelens.review.domain.ports import ReviewEvent
from codelens.review.infrastructure.event_bus import InMemoryEventBus


async def test_event_bus_publishes_to_all_subscribers() -> None:
    """Events are delivered to every active subscriber for a task."""

    bus = InMemoryEventBus()
    task_id = "review_" + "a" * 32

    queue1 = await bus.subscribe(task_id)
    queue2 = await bus.subscribe(task_id)

    event = ReviewEvent(
        event_id=1,
        task_id=task_id,
        event_type="review.created",
        payload={"status": "pending"},
    )
    await bus.publish(event)

    received1 = await queue1.get()
    received2 = await queue2.get()

    assert received1.event_id == 1
    assert received1.event_type == "review.created"
    assert received2.event_id == 1
    assert received2.event_type == "review.created"


async def test_event_bus_unsubscribe_removes_subscriber() -> None:
    """After unsubscribe, events are no longer delivered to that subscriber."""

    bus = InMemoryEventBus()
    task_id = "review_" + "b" * 32

    queue = await bus.subscribe(task_id)
    await bus.unsubscribe(task_id, queue)

    event = ReviewEvent(
        event_id=1,
        task_id=task_id,
        event_type="review.created",
        payload={},
    )
    await bus.publish(event)

    assert queue.empty()


async def test_event_bus_handles_multiple_tasks_independently() -> None:
    """Subscribers for one task do not receive events from another task."""

    bus = InMemoryEventBus()
    task1 = "review_" + "c" * 32
    task2 = "review_" + "d" * 32

    queue1 = await bus.subscribe(task1)
    queue2 = await bus.subscribe(task2)

    event1 = ReviewEvent(event_id=1, task_id=task1, event_type="review.created", payload={})
    event2 = ReviewEvent(event_id=2, task_id=task2, event_type="review.created", payload={})

    await bus.publish(event1)
    await bus.publish(event2)

    received1 = await queue1.get()
    received2 = await queue2.get()

    assert received1.task_id == task1
    assert received2.task_id == task2


async def test_event_bus_unsubscribe_is_idempotent() -> None:
    """Calling unsubscribe multiple times does not raise an error."""

    bus = InMemoryEventBus()
    task_id = "review_" + "e" * 32

    queue = await bus.subscribe(task_id)
    await bus.unsubscribe(task_id, queue)
    await bus.unsubscribe(task_id, queue)  # Should not raise


async def test_event_bus_publish_with_no_subscribers() -> None:
    """Publishing when no subscribers exist does not raise an error."""

    bus = InMemoryEventBus()
    task_id = "review_" + "f" * 32

    event = ReviewEvent(event_id=1, task_id=task_id, event_type="review.created", payload={})
    await bus.publish(event)  # Should not raise

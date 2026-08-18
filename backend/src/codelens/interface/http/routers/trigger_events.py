"""HTTP router for receiving trigger events from git hooks."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict

from codelens.interface.http.dependencies import HttpComponents, HttpProblem, get_components
from codelens.plugin.domain.models import HookEvent

router = APIRouter(prefix="/api/trigger-events", tags=["trigger-events"])
_LOGGER = logging.getLogger("codelens.trigger_events")

# Keep strong references to background tasks to prevent GC collection.
# asyncio only holds weak references; without this set, tasks can be
# silently cancelled mid-execution under GC pressure.
_background_tasks: set[asyncio.Task[None]] = set()


class TriggerEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    repository_path: str
    commit_sha: str | None = None
    push_ref: str | None = None


class TriggerEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    message: str
    task_ids: list[str]


@router.post("", response_model=TriggerEventResponse, status_code=status.HTTP_202_ACCEPTED)
async def receive_trigger_event(
    request: TriggerEventRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> TriggerEventResponse:
    """Receive a trigger event from a git hook script.

    This endpoint is called by git hook scripts installed in repositories.
    It validates the event and dispatches it to matching trigger plugins.

    Returns 202 Accepted immediately; review creation happens asynchronously.
    """

    from pathlib import Path

    # Validate event type
    try:
        hook_event = HookEvent(request.event)
    except ValueError:
        raise HttpProblem(
            400, "invalid_event_type", f"Unknown event type: {request.event}"
        ) from None

    # Build event payload based on event type
    event_payload = {}
    if hook_event == HookEvent.POST_COMMIT:
        if not request.commit_sha:
            raise HttpProblem(
                400, "missing_commit_sha", "post-commit event requires commit_sha"
            ) from None
        event_payload["commit_sha"] = request.commit_sha
    elif hook_event == HookEvent.PRE_PUSH:
        if not request.push_ref:
            raise HttpProblem(400, "missing_push_ref", "pre-push event requires push_ref") from None
        event_payload["push_ref"] = request.push_ref

    repository_path = Path(request.repository_path)

    _LOGGER.info(
        "Received %s event for %s",
        hook_event.value,
        repository_path,
    )

    # Dispatch to trigger orchestrator asynchronously (fire-and-forget)
    # This allows the endpoint to return 202 Accepted immediately
    async def _process_event() -> None:
        try:
            task_ids = await components.trigger_orchestrator.handle_event(
                event=hook_event,
                repository_path=repository_path,
                event_payload=event_payload,
            )
            # Filter out None values (plugins that didn't create reviews)
            created_task_ids = [tid for tid in task_ids if tid is not None]
            _LOGGER.info(
                "Dispatched %s event, created %d review(s)",
                hook_event.value,
                len(created_task_ids),
            )
        except Exception:
            _LOGGER.exception(
                "Failed to process %s event for %s",
                hook_event.value,
                repository_path,
            )

    # Fire-and-forget: create background task, retain reference to prevent GC
    task = asyncio.create_task(_process_event())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return TriggerEventResponse(
        status="accepted",
        message="Event accepted for processing",
        task_ids=[],  # Task IDs not available yet since processing is async
    )

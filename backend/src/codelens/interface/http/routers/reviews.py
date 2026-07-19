import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import StringConstraints

from codelens.interface.http.dependencies import (
    HttpComponents,
    HttpProblem,
    get_components,
)
from codelens.interface.http.dto import (
    CancelReviewRequest,
    CreateReviewRequest,
    ReviewResponse,
)
from codelens.review.application.commands import CreateReviewCommand

router = APIRouter(prefix="/api/reviews", tags=["reviews"])
_LOGGER = logging.getLogger("codelens.reviews")

TaskId = Annotated[
    str,
    StringConstraints(pattern=r"^review_[0-9a-f]{32}$", min_length=39, max_length=39),
]
_TERMINAL_EVENTS = {
    "review.completed",
    "review.partial",
    "review.failed",
    "review.canceled",
}


@router.post("", response_model=ReviewResponse, status_code=202)
async def create_review(
    request: CreateReviewRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewResponse:
    """Validate a source path, pin refs once, and create a durable review command."""

    _LOGGER.info("Review creation requested", extra={"scope_type": request.scope.type})
    repository = await components.repository_inspector.inspect(request.repository_path)
    record = await components.create_review.handle(
        CreateReviewCommand(
            repository=repository,
            scope=request.scope.to_domain(),
            selected_agent_versions=tuple(request.selected_agents),
        )
    )
    _LOGGER.info(
        "Review created",
        extra={"task_id": record.task_id, "scope_type": request.scope.type},
    )
    return ReviewResponse.from_domain(record)


@router.get("", response_model=list[ReviewResponse])
async def list_reviews(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[ReviewResponse]:
    """Return persistent visible Review workspaces in newest-first order."""

    return [
        ReviewResponse.from_domain(record)
        for record in await components.list_reviews.handle()
    ]


@router.get("/{task_id}", response_model=ReviewResponse)
async def get_review(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewResponse:
    """Return one path-free persisted review summary."""

    return ReviewResponse.from_domain(await components.get_review.handle(task_id))


@router.delete("/{task_id}", status_code=204)
async def delete_review(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> Response:
    """Hide one Review workspace and safely cancel it when still active."""

    await components.delete_review.handle(task_id)
    return Response(status_code=204)


@router.post("/{task_id}/cancel", response_model=ReviewResponse, status_code=202)
async def cancel_review(
    task_id: TaskId,
    _request: CancelReviewRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewResponse:
    """Persist cancellation intent; the singleton Worker performs propagation."""

    return ReviewResponse.from_domain(await components.cancel_review.handle(task_id))


@router.get("/{task_id}/report")
async def get_report(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> None:
    """Reserve the report contract until synthesis persistence lands in Phase 3."""

    await components.get_review.handle(task_id)
    raise HttpProblem(404, "report_not_ready", "The review report is not ready.")


@router.get("/{task_id}/findings")
async def list_findings(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[dict[str, object]]:
    """Return trusted Findings in stable severity/confidence/path order."""

    await components.get_review.handle(task_id)
    findings = await components.review_store.list_findings(task_id)
    return [asdict(finding) for finding in findings]


def _parse_last_event_id(raw_event_id: str | None) -> int:
    if raw_event_id is None:
        return 0
    if not raw_event_id.isascii() or not raw_event_id.isdecimal():
        raise HttpProblem(422, "invalid_event_id", "Last-Event-ID must be an integer.")
    event_id = int(raw_event_id)
    if event_id < 0 or event_id > 9_223_372_036_854_775_807:
        raise HttpProblem(422, "invalid_event_id", "Last-Event-ID is outside its range.")
    return event_id


async def _event_stream(
    request: Request,
    components: HttpComponents,
    task_id: str,
    after_event_id: int,
    task_is_terminal: bool,
) -> AsyncIterator[str]:
    current_id = after_event_id
    loop = asyncio.get_running_loop()
    next_keepalive = loop.time() + 15.0
    while True:
        rows = await components.events.list_after(task_id, after_event_id=current_id)
        for event in rows:
            current_id = event.event_id
            payload = json.dumps(event.payload, sort_keys=True, separators=(",", ":"))
            yield (f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n")
            if event.event_type in _TERMINAL_EVENTS:
                return
        if task_is_terminal:
            return
        if await request.is_disconnected():
            return
        if loop.time() >= next_keepalive:
            yield ": keep-alive\n\n"
            next_keepalive = loop.time() + 15.0
        await asyncio.sleep(0.1)


@router.get("/{task_id}/events")
async def stream_review_events(
    request: Request,
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Resume ordered redacted outbox events after one validated event ID."""

    review = await components.get_review.handle(task_id)
    after_event_id = _parse_last_event_id(last_event_id)
    return StreamingResponse(
        _event_stream(
            request,
            components,
            task_id,
            after_event_id,
            review.status in {"completed", "partial", "failed", "canceled"},
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )

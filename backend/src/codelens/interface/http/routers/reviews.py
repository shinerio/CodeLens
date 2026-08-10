import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from codelens.interface.http.dependencies import (
    HttpComponents,
    HttpProblem,
    get_components,
)
from codelens.interface.http.dto import (
    CancelReviewRequest,
    CreateReviewRequest,
    FindingSourcePreviewResponse,
    RetryReviewRequest,
    ReviewProcessReportResponse,
    ReviewResponse,
)
from codelens.review.application.commands import CreateReviewCommand
from codelens.review.application.process_report import ProcessTranscriptEntry, build_process_report
from codelens.review.domain.ports import ReviewRecord
from codelens.review.domain.review_plan import ReviewPlanNodeType

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
    "review.superseded",
}
_TERMINAL_STATUSES = {"completed", "partial", "failed", "canceled", "superseded"}


async def _review_response(review: ReviewRecord, components: HttpComponents) -> ReviewResponse:
    """Project public multi-Agent state exclusively from durable Plan and checkpoints."""

    plan_record = await components.review_plan_store.get(review.task_id)
    if plan_record is None:
        return ReviewResponse.from_domain(review)
    plan = plan_record.plan
    checkpoint_records = {
        item.node_key: item for item in await components.checkpoints.list_for_task(review.task_id)
    }
    coverage: dict[str, list[str]] = {
        "planned": [],
        "completed": [],
        "failed": [],
        "omitted": [],
    }
    for node in plan.nodes:
        if node.node_type is not ReviewPlanNodeType.REVIEWER:
            continue
        checkpoint = checkpoint_records.get(node.node_id)
        status = checkpoint.status if checkpoint is not None else "pending"
        target = (
            "completed"
            if status == "succeeded"
            else "failed"
            if status in {"failed", "timed_out", "canceled"}
            else "omitted"
            if status in {"skipped", "superseded"}
            else "planned"
        )
        coverage[target].append(node.agent_reference)
    decisions = await components.verdict_store.list_decisions(review.task_id)
    verdict_summary = {"accept": 0, "deny": 0, "merge": 0}
    for decision in decisions:
        verdict_summary[decision.outcome.value] += 1
    review_plan = json.loads(plan.canonical_json())
    review_plan["plan_hash"] = plan.plan_hash
    return ReviewResponse.from_domain(
        review,
        selected_agents=list(plan.reviewer_references),
        review_plan=review_plan,
        coverage=coverage,
        verdict_summary=verdict_summary,
    )


@router.post("", response_model=ReviewResponse, status_code=202)
async def create_review(
    request: CreateReviewRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewResponse:
    """Validate a source path, pin refs once, and create a durable review command."""

    _LOGGER.info("Review creation requested", extra={"scope_type": request.scope.type})
    repository = await components.repository_inspector.inspect(request.repository_path)
    try:
        record = await components.create_review.handle(
            CreateReviewCommand(
                repository=repository,
                scope=request.scope.to_domain(),
                review_profile=request.review_profile_snapshot(),
                prompt_locale=request.prompt_locale,
                external_context=request.external_context,
            )
        )
    except ValueError as error:
        if str(error) != "a ReviewTask requires at least one frozen target path":
            raise
        raise HttpProblem(
            422,
            "empty_review_scope",
            "No eligible changed files were found. Choose two commits or branches with changes.",
        ) from None
    _LOGGER.info(
        "Review created",
        extra={"task_id": record.task_id, "scope_type": request.scope.type},
    )
    return await _review_response(record, components)


@router.get("", response_model=list[ReviewResponse])
async def list_reviews(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[ReviewResponse]:
    """Return persistent visible Review workspaces in newest-first order."""

    results = []
    for record in await components.list_reviews.handle():
        try:
            response = await _review_response(record, components)
            results.append(response)
        except Exception as exc:
            _LOGGER.warning(
                "Failed to serialize review %s, skipping: %s",
                record.task_id,
                exc,
            )
    return results


@router.get("/{task_id}", response_model=ReviewResponse)
async def get_review(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewResponse:
    """Return one path-free persisted review summary."""

    return await _review_response(await components.get_review.handle(task_id), components)


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

    return await _review_response(await components.cancel_review.handle(task_id), components)


@router.post("/{task_id}/retry", response_model=ReviewResponse, status_code=202)
async def retry_review(
    task_id: TaskId,
    _request: RetryReviewRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewResponse:
    """Create and enqueue an independent Review from one failed task's frozen input."""

    return await _review_response(await components.retry_review.handle(task_id), components)


@router.get("/{task_id}/report")
async def get_report(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> None:
    """Reserve the report contract until synthesis persistence lands in Phase 3."""

    await components.get_review.handle(task_id)
    raise HttpProblem(404, "report_not_ready", "The review report is not ready.")


@router.get("/{task_id}/transcript")
async def get_transcript(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[dict[str, object]]:
    """Return the credential-redacted execution conversation for one Review."""

    review = await components.get_review.handle(task_id)
    entries = (
        await components.transcripts.list(task_id)
        if review.status in {"completed", "partial", "failed", "canceled"}
        else await components.worker_transcripts.list(task_id)
    )
    return [entry.model_dump(mode="json") for entry in entries]


@router.get("/{task_id}/process-report", response_model=ReviewProcessReportResponse)
async def get_process_report(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewProcessReportResponse:
    """Return deterministic usage and tool metrics after one Review reaches a terminal state."""

    review = await components.get_review.handle(task_id)
    if review.status not in _TERMINAL_STATUSES:
        raise HttpProblem(
            409,
            "process_report_not_ready",
            "The review process report is available after execution finishes.",
        )
    entries = await components.transcripts.list(task_id)
    if not entries:
        raise HttpProblem(
            409,
            "process_report_not_ready",
            "The terminal review transcript has not been persisted yet.",
        )
    findings = await components.review_store.list_findings(task_id)
    report = build_process_report(
        task_id=task_id,
        status=review.status,
        entries=tuple(
            ProcessTranscriptEntry(
                sequence=entry.sequence,
                kind=entry.kind,
                content=entry.content,
                created_at=entry.created_at,
                metadata=entry.metadata,
            )
            for entry in entries
        ),
        finding_count=len(findings),
    )
    return ReviewProcessReportResponse.from_application(report)


@router.get("/{task_id}/findings")
async def list_findings(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[dict[str, object]]:
    """Return trusted Findings in stable severity/confidence/path order."""

    await components.get_review.handle(task_id)
    findings = await components.review_store.list_findings(task_id)
    return [asdict(finding) for finding in findings]


@router.get("/{task_id}/findings/{finding_id}/source", response_model=FindingSourcePreviewResponse)
async def get_finding_source(
    task_id: TaskId,
    finding_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> FindingSourcePreviewResponse:
    """Return both available pinned source versions for one persisted review opinion."""

    try:
        preview = await components.finding_source_preview.get(task_id, finding_id)
    except KeyError:
        raise HttpProblem(
            404, "finding_source_not_found", "The requested finding source is unavailable."
        ) from None
    return FindingSourcePreviewResponse(**asdict(preview))


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    task_id: str
    success: bool
    output_path: str | None
    error: str | None
    exported_at: str


@router.post("/{task_id}/export", response_model=ExportResponse)
async def export_findings(
    task_id: TaskId,
    request: ExportRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ExportResponse:
    """Trigger one report plugin export for a completed review."""

    result = await components.export_orchestrator.export_findings(task_id, request.plugin_id)
    return ExportResponse(
        plugin_id=result.plugin_id,
        task_id=result.task_id,
        success=result.success,
        output_path=result.output_path,
        error=result.error,
        exported_at=result.exported_at.isoformat(),
    )


@router.get("/{task_id}/exports", response_model=list[ExportResponse])
async def list_exports(
    task_id: TaskId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[ExportResponse]:
    """Return all export history entries for a review task."""

    entries = await components.export_history.list_by_task(task_id)
    return [
        ExportResponse(
            plugin_id=entry.plugin_id,
            task_id=entry.task_id,
            success=entry.success,
            output_path=entry.output_path,
            error=entry.error,
            exported_at=entry.exported_at.isoformat(),
        )
        for entry in entries
    ]


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
    queue = await components.event_bus.subscribe(task_id)
    try:
        # Replay events from database (catch-up phase).
        # A task may have multiple terminal events in its history (e.g.
        # review.partial → review.failed → review.completed) when it was
        # recovered after an initial failure. The SSE replay must only
        # send the LAST terminal event so the frontend observes the final
        # status, not a stale intermediate one.
        replay_events = await components.events.list_after(task_id, after_event_id=after_event_id)
        last_terminal_idx = -1
        for idx, event in enumerate(replay_events):
            if event.event_type in _TERMINAL_EVENTS:
                last_terminal_idx = idx

        current_id = after_event_id
        for idx, event in enumerate(replay_events):
            current_id = event.event_id
            if event.event_type in _TERMINAL_EVENTS and idx != last_terminal_idx:
                continue
            payload = json.dumps(event.payload, sort_keys=True, separators=(",", ":"))
            yield f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n"
            if idx == last_terminal_idx:
                return

        # If task is already terminal, we're done
        if task_is_terminal:
            return

        # Stream live events from bus queue
        loop = asyncio.get_running_loop()
        next_keepalive = loop.time() + 15.0
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                # Skip events we've already seen (from replay)
                if event.event_id <= current_id:
                    continue
                current_id = event.event_id
                payload = json.dumps(event.payload, sort_keys=True, separators=(",", ":"))
                yield f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n"
                if event.event_type in _TERMINAL_EVENTS:
                    return
            except TimeoutError:
                # No event received, check keep-alive
                pass

            # Send keep-alive if needed
            if loop.time() >= next_keepalive:
                yield ": keep-alive\n\n"
                next_keepalive = loop.time() + 15.0

            # Check if client disconnected
            if await request.is_disconnected():
                return
    finally:
        await components.event_bus.unsubscribe(task_id, queue)


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

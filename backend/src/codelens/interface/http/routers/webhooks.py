"""Platform-agnostic webhook receiver endpoint."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from codelens.interface.http.dependencies import (
    HttpComponents,
    get_components,
)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
_LOGGER = logging.getLogger("codelens.webhooks")


@router.post("/{platform}", status_code=202)
async def receive_webhook(
    platform: str,
    request: Request,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> dict[str, Any]:
    """Receive and dispatch a webhook event to matching plugins.

    This endpoint is platform-agnostic: it does not parse the payload,
    assume any platform-specific structure, or perform built-in signature
    verification. All validation and interpretation is delegated to the
    matching plugin's trigger capability.

    The raw request body and headers are forwarded as-is to the
    TriggerOrchestrator, which routes to plugins whose manifest.platform
    matches the URL path parameter.
    """

    _LOGGER.info("Webhook received for platform: %s", platform)

    # Read raw body and headers
    raw_body = await request.body()
    headers: dict[str, str] = dict(request.headers)

    # Parse payload as JSON (best-effort; plugins receive both raw and parsed)
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        payload = {"_raw": raw_body.decode("utf-8", errors="replace")}

    # Dispatch to orchestrator
    task_ids = await components.trigger_orchestrator.handle_webhook(
        platform=platform,
        payload=payload,
        headers=headers,
    )

    created = [tid for tid in task_ids if tid is not None]
    _LOGGER.info(
        "Webhook dispatched: platform=%s, reviews_created=%d",
        platform,
        len(created),
    )

    return {
        "platform": platform,
        "received": True,
        "reviews_created": len(created),
        "task_ids": created,
    }

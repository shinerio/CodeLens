"""HTTP contract for localized built-in reviewer prompt customization."""

from collections.abc import Awaitable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import StringConstraints

from codelens.interface.http.dependencies import HttpComponents, get_components
from codelens.interface.http.dto import ReviewerPromptResponse, UpdateReviewerPromptRequest
from codelens.reviewer_catalog.application.prompt_settings import ReviewerPromptView
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog

router = APIRouter(prefix="/api/reviewer-prompts", tags=["reviewer-prompts"])
AgentId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$", max_length=64),
]
Locale = Literal["en", "zh-CN"]
Version = Annotated[int, Query(ge=1)]


def _agent(agent_id: str, version: int) -> AgentVersion:
    """Resolve an HTTP identity only through the immutable built-in catalog."""

    try:
        return builtin_agent_catalog()[f"{agent_id}:v{version}"]
    except KeyError as error:
        raise HTTPException(status_code=404, detail="reviewer prompt is unavailable") from error


def _response(view: ReviewerPromptView) -> ReviewerPromptResponse:
    """Map the editable system prompt contract without exposing internal phase prompts."""

    return ReviewerPromptResponse(
        agent_id=view.agent_id,
        version=view.version,
        locale=view.locale,
        system_prompt=view.system_prompt,
        prompt=view.prompt,
        is_custom=view.is_custom,
    )


async def _resolve(view: Awaitable[ReviewerPromptView]) -> ReviewerPromptView:
    try:
        return await view
    except ValueError as error:
        raise HTTPException(status_code=404, detail="reviewer prompt is unavailable") from error


@router.get("/{agent_id}", response_model=ReviewerPromptResponse)
async def get_reviewer_prompt(
    agent_id: AgentId,
    locale: Locale,
    components: Annotated[HttpComponents, Depends(get_components)],
    version: Version = 2,
) -> ReviewerPromptResponse:
    return _response(
        await _resolve(components.reviewer_prompts.get(_agent(agent_id, version), locale))
    )


@router.put("/{agent_id}", response_model=ReviewerPromptResponse)
async def update_reviewer_prompt(
    agent_id: AgentId,
    locale: Locale,
    request: UpdateReviewerPromptRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
    version: Version = 2,
) -> ReviewerPromptResponse:
    return _response(
        await _resolve(
            components.reviewer_prompts.update(_agent(agent_id, version), locale, request.prompt)
        )
    )


@router.delete("/{agent_id}", response_model=ReviewerPromptResponse)
async def reset_reviewer_prompt(
    agent_id: AgentId,
    locale: Locale,
    components: Annotated[HttpComponents, Depends(get_components)],
    version: Version = 2,
) -> ReviewerPromptResponse:
    return _response(
        await _resolve(components.reviewer_prompts.reset(_agent(agent_id, version), locale))
    )

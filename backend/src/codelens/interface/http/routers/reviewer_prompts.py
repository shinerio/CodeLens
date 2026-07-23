"""HTTP contract for localized built-in reviewer prompt customization."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import StringConstraints

from codelens.interface.http.dependencies import HttpComponents, get_components
from codelens.interface.http.dto import ReviewerPromptResponse, UpdateReviewerPromptRequest
from codelens.reviewer_catalog.application.prompt_settings import ReviewerPromptView

router = APIRouter(prefix="/api/reviewer-prompts", tags=["reviewer-prompts"])
AgentId = Annotated[str, StringConstraints(pattern=r"^correctness$", max_length=64)]
Locale = Literal["en", "zh-CN"]


def _response(view: ReviewerPromptView) -> ReviewerPromptResponse:
    return ReviewerPromptResponse(**view.__dict__)


@router.get("/{agent_id}", response_model=ReviewerPromptResponse)
async def get_reviewer_prompt(
    agent_id: AgentId,
    locale: Locale,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewerPromptResponse:
    return _response(await components.reviewer_prompts.get(agent_id, locale))


@router.put("/{agent_id}", response_model=ReviewerPromptResponse)
async def update_reviewer_prompt(
    agent_id: AgentId,
    locale: Locale,
    request: UpdateReviewerPromptRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewerPromptResponse:
    return _response(await components.reviewer_prompts.update(agent_id, locale, request.prompt))


@router.delete("/{agent_id}", response_model=ReviewerPromptResponse)
async def reset_reviewer_prompt(
    agent_id: AgentId,
    locale: Locale,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewerPromptResponse:
    return _response(await components.reviewer_prompts.reset(agent_id, locale))

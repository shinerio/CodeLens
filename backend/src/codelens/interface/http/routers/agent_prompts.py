"""HTTP contract for localized built-in agent prompt customization."""

from collections.abc import Awaitable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import StringConstraints

from codelens.interface.http.dependencies import HttpComponents, get_components
from codelens.interface.http.dto import (
    AgentPromptCatalogEntryResponse,
    AgentPromptResponse,
    UpdateAgentPromptRequest,
)
from codelens.reviewer_catalog.application.prompt_settings import AgentPromptView
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog

router = APIRouter(prefix="/api/agent-prompts", tags=["agent-prompts"])
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
        raise HTTPException(status_code=404, detail="agent prompt is unavailable") from error


def _response(view: AgentPromptView) -> AgentPromptResponse:
    """Map the editable system prompt contract without exposing internal phase prompts."""

    return AgentPromptResponse(
        agent_id=view.agent_id,
        version=view.version,
        locale=view.locale,
        system_prompt=view.system_prompt,
        prompt=view.prompt,
        is_custom=view.is_custom,
    )


async def _resolve(view: Awaitable[AgentPromptView]) -> AgentPromptView:
    try:
        return await view
    except ValueError as error:
        raise HTTPException(status_code=404, detail="agent prompt is unavailable") from error


@router.get("", response_model=list[AgentPromptCatalogEntryResponse])
async def list_agent_prompt_catalog() -> list[AgentPromptCatalogEntryResponse]:
    """List every built-in agent whose prompt is editable, including internal DAG roles."""

    return [
        AgentPromptCatalogEntryResponse(
            reference=agent.reference,
            agent_id=agent.agent_id,
            version=agent.version,
            role=agent.role.value,
            dimensions=list(agent.dimensions),
            capability_readiness="ready",
        )
        for agent in builtin_agent_catalog().values()
    ]


@router.get("/{agent_id}", response_model=AgentPromptResponse)
async def get_agent_prompt(
    agent_id: AgentId,
    locale: Locale,
    components: Annotated[HttpComponents, Depends(get_components)],
    version: Version = 2,
) -> AgentPromptResponse:
    return _response(
        await _resolve(components.agent_prompts.get(_agent(agent_id, version), locale))
    )


@router.put("/{agent_id}", response_model=AgentPromptResponse)
async def update_agent_prompt(
    agent_id: AgentId,
    locale: Locale,
    request: UpdateAgentPromptRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
    version: Version = 2,
) -> AgentPromptResponse:
    return _response(
        await _resolve(
            components.agent_prompts.update(_agent(agent_id, version), locale, request.prompt)
        )
    )


@router.delete("/{agent_id}", response_model=AgentPromptResponse)
async def reset_agent_prompt(
    agent_id: AgentId,
    locale: Locale,
    components: Annotated[HttpComponents, Depends(get_components)],
    version: Version = 2,
) -> AgentPromptResponse:
    return _response(
        await _resolve(components.agent_prompts.reset(_agent(agent_id, version), locale))
    )

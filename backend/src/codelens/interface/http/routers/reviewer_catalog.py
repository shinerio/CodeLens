from typing import Literal

from fastapi import APIRouter

from codelens.interface.http.dto import StrictDto
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog

router = APIRouter(prefix="/api/reviewer-catalog", tags=["reviewer-catalog"])


class ReviewerCatalogEntryResponse(StrictDto):
    reference: str
    agent_id: str
    version: int
    dimensions: list[str]
    cost_class: Literal["balanced"]
    planner_eligible: bool
    capability_readiness: Literal["ready"]
    is_legacy: bool


@router.get("", response_model=list[ReviewerCatalogEntryResponse])
async def list_reviewer_catalog() -> list[ReviewerCatalogEntryResponse]:
    """Expose only public immutable Reviewer versions, never internal DAG roles."""

    return [
        ReviewerCatalogEntryResponse(
            reference=agent.reference,
            agent_id=agent.agent_id,
            version=agent.version,
            dimensions=list(agent.dimensions),
            cost_class="balanced",
            planner_eligible=agent.planner_eligible,
            capability_readiness="ready",
            is_legacy=agent.is_legacy,
        )
        for agent in builtin_agent_catalog().values()
        if agent.is_public
    ]

from pathlib import Path

from codelens.workspace.domain.models import ReviewScope
from codelens.workspace.domain.ports import ScopePlan, ScopePlanningPort


class ScopePlanner:
    """Pin a user-selected review scope before durable task creation."""

    def __init__(self, planning: ScopePlanningPort) -> None:
        self._planning = planning

    async def plan(self, repository: Path, scope: ReviewScope) -> ScopePlan:
        """Resolve scope labels once and return only immutable executable state."""

        return await self._planning.plan_scope(repository, scope)

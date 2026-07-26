"""Application service for configurable repository instruction file limits."""

import asyncio
from typing import Protocol

from codelens.instruction_policy.domain.models import InstructionLineLimits


class InstructionLineLimitsStorePort(Protocol):
    """Persist and provide the current instruction file limits."""

    def get_line_limits(self) -> InstructionLineLimits:
        """Load the persisted limits or return product defaults."""

        raise NotImplementedError

    def save_line_limits(self, limits: InstructionLineLimits) -> None:
        """Atomically replace the persisted limits."""

        raise NotImplementedError


class InstructionSettingsService:
    """Validate and persist instruction limits outside the event loop."""

    def __init__(self, store: InstructionLineLimitsStorePort) -> None:
        self._store = store

    async def get(self) -> InstructionLineLimits:
        """Return the limits currently used by new Review snapshots."""

        return await asyncio.to_thread(self._store.get_line_limits)

    async def update(
        self,
        *,
        root_max_lines: int,
        nested_max_lines: int,
    ) -> InstructionLineLimits:
        """Validate and atomically persist replacement limits."""

        limits = InstructionLineLimits(
            root_max_lines=root_max_lines,
            nested_max_lines=nested_max_lines,
        )
        await asyncio.to_thread(self._store.save_line_limits, limits)
        return limits

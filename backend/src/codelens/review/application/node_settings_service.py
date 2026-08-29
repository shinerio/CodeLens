"""Application service for process-level resource limits."""

import asyncio

from codelens.bootstrap.node_settings import NodeSettings
from codelens.review.domain.ports import NodeSettingsStorePort


class NodeSettingsService:
    """Validate and persist node settings outside the event loop.

    Persisted values apply on the next Worker process restart; they do not
    reconfigure the running ``MemoryGuard`` or ``WorkerSemaphores``.
    """

    def __init__(self, store: NodeSettingsStorePort) -> None:
        self._store = store

    async def get(self) -> NodeSettings:
        """Return the persisted node settings (or product defaults)."""

        return await asyncio.to_thread(self._store.get_node_settings)

    async def update(
        self,
        *,
        memory_limit_mb: int | None = None,
        memory_check_interval_seconds: float | None = None,
        memory_cleanup_threshold_ratio: float | None = None,
        memory_reject_threshold_ratio: float | None = None,
        max_active_reviews: int | None = None,
        max_active_agent_runs: int | None = None,
        max_agent_runs_per_review: int | None = None,
    ) -> NodeSettings:
        """Merge partial updates into the current settings and atomically persist."""

        current = await asyncio.to_thread(self._store.get_node_settings)
        settings = NodeSettings(
            memory_limit_mb=(
                memory_limit_mb
                if memory_limit_mb is not None
                else current.memory_limit_mb
            ),
            memory_check_interval_seconds=(
                memory_check_interval_seconds
                if memory_check_interval_seconds is not None
                else current.memory_check_interval_seconds
            ),
            memory_cleanup_threshold_ratio=(
                memory_cleanup_threshold_ratio
                if memory_cleanup_threshold_ratio is not None
                else current.memory_cleanup_threshold_ratio
            ),
            memory_reject_threshold_ratio=(
                memory_reject_threshold_ratio
                if memory_reject_threshold_ratio is not None
                else current.memory_reject_threshold_ratio
            ),
            max_active_reviews=(
                max_active_reviews
                if max_active_reviews is not None
                else current.max_active_reviews
            ),
            max_active_agent_runs=(
                max_active_agent_runs
                if max_active_agent_runs is not None
                else current.max_active_agent_runs
            ),
            max_agent_runs_per_review=(
                max_agent_runs_per_review
                if max_agent_runs_per_review is not None
                else current.max_agent_runs_per_review
            ),
        )
        await asyncio.to_thread(self._store.save_node_settings, settings)
        return settings

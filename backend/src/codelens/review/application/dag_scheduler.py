from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from codelens.review.domain.review_plan import (
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)

_TERMINAL_NODE_STATUSES = {
    "succeeded",
    "failed",
    "timed_out",
    "canceled",
    "skipped",
    "superseded",
}


class AgentRunStatusView(Protocol):
    """Expose persisted node identity and status for DAG reduction."""

    @property
    def node_key(self) -> str: ...

    @property
    def status(self) -> str: ...


class DagCheckpointPort(Protocol):
    """Persist and query the complete host-owned DAG."""

    async def ensure_plan_nodes(
        self,
        plan: ReviewPlan,
        *,
        capability_fingerprints: dict[str, str] | None = None,
    ) -> None: ...

    async def list_for_task(self, task_id: str) -> tuple[AgentRunStatusView, ...]: ...


def reviewer_stage_outcome(
    nodes: Sequence[AgentRunStatusView],
) -> Literal["continue", "partial", "failed"]:
    """Reduce persisted Reviewer terminals without relying on in-memory task results."""

    succeeded = sum(node.status == "succeeded" for node in nodes)
    failed = sum(node.status in {"failed", "timed_out"} for node in nodes)
    if succeeded == 0 and failed > 0:
        return "failed"
    if failed > 0:
        return "partial"
    return "continue"


class PersistedDagScheduler:
    """Select ready nodes only from an immutable Plan and persisted checkpoint state."""

    def __init__(self, plan: ReviewPlan, checkpoints: DagCheckpointPort) -> None:
        self._plan = plan
        self._checkpoints = checkpoints

    async def initialize(
        self, capability_fingerprints: Mapping[str, str] | None = None
    ) -> None:
        """Idempotently create every prebuilt node before any model invocation."""

        await self._checkpoints.ensure_plan_nodes(
            self._plan,
            capability_fingerprints=dict(capability_fingerprints or {}),
        )

    async def next_ready_nodes(self, task_id: str) -> tuple[ReviewPlanNode, ...]:
        """Return stable pending nodes whose role-specific dependencies are durable."""

        if task_id != self._plan.task_id:
            raise ValueError("Review Plan belongs to another task")
        records = await self._checkpoints.list_for_task(task_id)
        status_by_node = {record.node_key: record.status for record in records}
        expected = {node.node_id for node in self._plan.nodes}
        if set(status_by_node) != expected:
            raise ValueError("persisted checkpoints do not match the Review Plan")
        ready = tuple(
            node
            for node in self._plan.nodes
            if status_by_node[node.node_id] == "pending"
            and self._dependencies_allow(node, status_by_node)
        )
        return tuple(sorted(ready, key=lambda node: (node.pass_index, node.node_id)))

    @staticmethod
    def _dependencies_allow(
        node: ReviewPlanNode, status_by_node: Mapping[str, str]
    ) -> bool:
        dependency_statuses = tuple(
            status_by_node[dependency] for dependency in node.depends_on
        )
        if not dependency_statuses:
            return True
        if node.node_type is ReviewPlanNodeType.RESOLVER:
            return all(
                status in _TERMINAL_NODE_STATUSES for status in dependency_statuses
            ) and any(status == "succeeded" for status in dependency_statuses)
        return all(status == "succeeded" for status in dependency_statuses)

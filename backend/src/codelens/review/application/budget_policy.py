import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    FrozenAgentExecutionSpec,
)
from codelens.review.domain.review_strategy import BudgetProfile
from codelens.shared.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Freeze one task-level capacity envelope for deterministic planning.

    ``max_task_seconds`` is an estimation budget, not a cross-Agent hard
    deadline. Individual nodes retain explicit frozen timeouts.
    """

    max_reviewers: int
    max_model_nodes: int
    per_review_concurrency: int
    max_total_tokens: int
    max_node_output_tokens: int
    max_turns_per_node: int
    max_tool_calls_per_node: int
    max_task_seconds: int
    max_verifier_clusters: int


BUDGET_POLICY_V1: Mapping[BudgetProfile, BudgetLimits] = {
    BudgetProfile.LEAN: BudgetLimits(1, 2, 1, 100_000, 8_000, 12, 80, 300, 0),
    BudgetProfile.STANDARD: BudgetLimits(
        3, 6, 3, 400_000, 16_000, 20, 240, 900, 12
    ),
    BudgetProfile.DEEP: BudgetLimits(
        7, 10, 4, 1_200_000, 24_000, 30, 600, 1_800, 40
    ),
}


class BudgetExceededError(DomainError):
    """Reject a node whose frozen or reported usage exceeds task capacity."""

    code = "review_budget_exceeded"

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class TokenEstimatorPort(Protocol):
    """Estimate frozen provider-neutral input with a versioned model."""

    @property
    def estimator_version(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    def estimate(self, payload: bytes, model_profile_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    """Expose the immutable worst-case capacity reserved for one logical node."""

    node_id: str
    estimated_input_tokens: int
    max_output_tokens: int
    max_tool_calls: int
    estimator_version: str
    model_version: str


@dataclass(slots=True)
class _ReservationState:
    reservation: BudgetReservation
    max_input_tokens: int
    is_reconciled: bool = False
    actual_tokens: int = 0


class TaskBudgetLedger:
    """Atomically reserve worst-case node capacity and reconcile reported usage."""

    def __init__(self, limits: BudgetLimits) -> None:
        self._limits = limits
        self._states: dict[str, _ReservationState] = {}
        self._lock = asyncio.Lock()

    async def reserve(
        self,
        *,
        node_id: str,
        input_payload: bytes,
        execution_spec: FrozenAgentExecutionSpec,
        estimator: TokenEstimatorPort,
    ) -> BudgetReservation:
        """Reserve estimated input plus worst-case output, node, and tool capacity."""

        estimated_input = estimator.estimate(
            input_payload, execution_spec.agent.model_profile_id
        )
        if estimated_input < 0:
            raise ValueError("Token estimator returned a negative value")
        node_limits = execution_spec.execution_limits
        if estimated_input > node_limits.max_input_tokens:
            raise BudgetExceededError("estimated_input_tokens_exceeded")
        if (
            node_limits.max_output_tokens > self._limits.max_node_output_tokens
            or node_limits.max_tool_calls > self._limits.max_tool_calls_per_node
        ):
            raise BudgetExceededError("node_limits_exceeded")
        reservation = BudgetReservation(
            node_id=node_id,
            estimated_input_tokens=estimated_input,
            max_output_tokens=node_limits.max_output_tokens,
            max_tool_calls=node_limits.max_tool_calls,
            estimator_version=estimator.estimator_version,
            model_version=estimator.model_version,
        )
        async with self._lock:
            existing = self._states.get(node_id)
            if existing is not None:
                if existing.reservation != reservation:
                    raise ValueError("logical node already has a different budget reservation")
                return existing.reservation
            if len(self._states) >= self._limits.max_model_nodes:
                raise BudgetExceededError("model_node_capacity_exceeded")
            reserved_tokens = sum(
                state.actual_tokens
                if state.is_reconciled
                else state.reservation.estimated_input_tokens
                + state.reservation.max_output_tokens
                for state in self._states.values()
            )
            if (
                reserved_tokens + estimated_input + node_limits.max_output_tokens
                > self._limits.max_total_tokens
            ):
                raise BudgetExceededError("task_token_capacity_exceeded")
            reserved_tools = sum(
                state.reservation.max_tool_calls for state in self._states.values()
            )
            if (
                reserved_tools + node_limits.max_tool_calls
                > self._limits.max_model_nodes
                * self._limits.max_tool_calls_per_node
            ):
                raise BudgetExceededError("task_tool_capacity_exceeded")
            self._states[node_id] = _ReservationState(
                reservation, node_limits.max_input_tokens
            )
            return reservation

    async def reconcile(
        self,
        node_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int,
    ) -> None:
        """Replace one reservation with provider usage or fail on a frozen limit breach."""

        if min(input_tokens, output_tokens, tool_calls) < 0:
            raise ValueError("reported budget usage cannot be negative")
        async with self._lock:
            state = self._states.get(node_id)
            if state is None:
                raise ValueError("logical node has no budget reservation")
            if state.is_reconciled:
                raise ValueError("logical node budget was already reconciled")
            if input_tokens > state.max_input_tokens:
                raise BudgetExceededError("reported_input_tokens_exceeded")
            if output_tokens > state.reservation.max_output_tokens:
                raise BudgetExceededError("reported_output_tokens_exceeded")
            if tool_calls > state.reservation.max_tool_calls:
                raise BudgetExceededError("reported_tool_calls_exceeded")
            actual_tokens = input_tokens + output_tokens
            other_tokens = sum(
                other.actual_tokens
                if other.is_reconciled
                else other.reservation.estimated_input_tokens
                + other.reservation.max_output_tokens
                for key, other in self._states.items()
                if key != node_id
            )
            if other_tokens + actual_tokens > self._limits.max_total_tokens:
                raise BudgetExceededError("reported_task_tokens_exceeded")
            state.actual_tokens = actual_tokens
            state.is_reconciled = True

    async def release(self, node_id: str) -> None:
        """Release an unreconciled reservation after a node fails before completion."""

        async with self._lock:
            state = self._states.get(node_id)
            if state is None:
                return
            if state.is_reconciled:
                raise ValueError("reconciled budget usage cannot be released")
            del self._states[node_id]


class BudgetPolicyCatalog:
    """Resolve immutable versioned budget limits and validate whole DAG shapes."""

    def __init__(self, version: int, limits: Mapping[BudgetProfile, BudgetLimits]) -> None:
        if version < 1 or set(limits) != set(BudgetProfile):
            raise ValueError("Budget Policy Catalog must define every profile at one version")
        self.version = version
        self._limits = dict(limits)

    @classmethod
    def version_one(cls) -> "BudgetPolicyCatalog":
        return cls(1, BUDGET_POLICY_V1)

    def limits(self, profile: BudgetProfile) -> BudgetLimits:
        return self._limits[profile]

    def validate_shape(
        self,
        *,
        profile: BudgetProfile,
        selection_mode: str,
        reviewer_count: int,
        is_multi_specialist: bool,
    ) -> BudgetLimits:
        limits = self.limits(profile)
        if reviewer_count < 1 or reviewer_count > limits.max_reviewers:
            raise ValueError("reviewer team exceeds the frozen budget")
        model_nodes = reviewer_count
        if selection_mode == "adaptive":
            model_nodes += 1
        if is_multi_specialist:
            model_nodes += 2
        if model_nodes > limits.max_model_nodes:
            raise ValueError("Review DAG exceeds the frozen model-node budget")
        return limits

    def node_limits(
        self,
        profile: BudgetProfile,
        *,
        provider_max_input_tokens: int,
        provider_max_output_tokens: int,
        provider_timeout_seconds: float,
        max_tool_result_bytes: int,
    ) -> AgentExecutionLimits:
        limits = self.limits(profile)
        return AgentExecutionLimits(
            max_turns=limits.max_turns_per_node,
            max_tool_calls=limits.max_tool_calls_per_node,
            max_input_tokens=min(provider_max_input_tokens, limits.max_total_tokens),
            max_output_tokens=min(
                provider_max_output_tokens, limits.max_node_output_tokens
            ),
            timeout_seconds=provider_timeout_seconds,
            max_tool_result_bytes=max_tool_result_bytes,
        )

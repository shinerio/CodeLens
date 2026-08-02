"""Assemble model-visible built-ins from one frozen Capability Profile."""

from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol

from agents import FunctionTool, Tool, function_tool
from pydantic import StringConstraints

from codelens.capabilities.domain.models import (
    FrozenAgentExecutionSpec,
    ToolContractReference,
)
from codelens.findings.domain.candidates import CandidateFindingBatch
from codelens.review.application.settings import ReviewCompletionSettings
from codelens.review.domain.errors import PermanentAgentOutputError
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.comment_collector import ReviewCommentCollector
from codelens.review.infrastructure.comment_collector_v2 import ReviewCommentCollectorV2
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.review.infrastructure.tool_contract import enforce_tool_execution_limits
from codelens.workspace.domain.models import ReviewSnapshot
from codelens.workspace.infrastructure.git_cli import GitCli


@dataclass(frozen=True)
class ToolExecutionLimits:
    """Configure the one shared limiter applied after frozen tool selection."""

    max_tool_calls: int
    max_identical_tool_results: int
    tool_timeout_seconds: float
    tool_loop_warning_template: str

    def __post_init__(self) -> None:
        if self.max_tool_calls < 1:
            raise ValueError("tool call limit must be positive")
        if self.max_identical_tool_results < 2:
            raise ValueError("identical tool result limit must be at least two")
        if self.tool_timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")

    @classmethod
    def testing(cls) -> "ToolExecutionLimits":
        """Return deterministic limits suitable for isolated tool assembly tests."""

        return cls(100, 3, 30.0, "Repeated {repeated_count}; {remaining} attempts remain.")


class RoleOutputState(Protocol):
    """Expose completion and canonicalizable output for one internal Agent role."""

    @property
    def is_completed(self) -> bool: ...

    @property
    def incomplete_review_files(self) -> tuple[str, ...]: ...

    def final_output(self) -> object: ...


@dataclass(frozen=True)
class RoleOutputToolBinding:
    """Bind one host-owned role output tool to a stable CodeLens contract."""

    contract: ToolContractReference
    tool: Tool
    state: RoleOutputState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool, FunctionTool):
            raise TypeError("role output contracts require function tools")
        if self.tool.name != self.contract.name:
            raise ValueError("role output tool name does not match its contract")


@dataclass(frozen=True)
class ReviewerOutputState:
    """Retain one reviewer's versioned comments and shared completion controls."""

    contract_version: Literal["1", "2"]
    comment_collector: ReviewCommentCollector | ReviewCommentCollectorV2
    completion_controls: ReviewCommentCollector

    @property
    def is_completed(self) -> bool:
        """Return whether this Agent Run made one accepted task_done call."""

        return self.completion_controls.is_completed

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        """Return files omitted only after completion retry exhaustion."""

        return self.completion_controls.incomplete_review_files

    def final_output(self) -> dict[str, object] | CandidateFindingBatch:
        """Return the collector output matching the frozen Comment contract."""

        if self.contract_version == "1":
            if not isinstance(self.comment_collector, ReviewCommentCollector):
                raise RuntimeError("Comment v1 output state has the wrong collector")
            return self.comment_collector.finding_batch()
        if not isinstance(self.comment_collector, ReviewCommentCollectorV2):
            raise RuntimeError("Comment v2 output state has the wrong collector")
        return self.comment_collector.candidate_batch()


def _submission_stub(name: str, description: str) -> Tool:
    Submission = Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
    ]

    @function_tool(name_override=name, description_override=description)
    async def submit_tool(submission: Submission) -> str:
        """Accept one bounded opaque test submission without external side effects."""

        return submission

    return submit_tool


@dataclass
class RuntimeToolContext:
    """Carry only frozen, bounded adapters used to construct one Agent tool set.

    Internal Planner, Resolver, and Verifier output tools are injected as typed
    stable bindings. Production composition fails closed when a frozen profile
    requests one that has not been supplied.
    """

    snapshot: ReviewSnapshot
    git: GitCli
    tool_descriptions: dict[str, str]
    tool_limits: ToolLimits
    completion_settings: ReviewCompletionSettings
    call_limits: ToolExecutionLimits
    role_output_tools: tuple[RoleOutputToolBinding, ...] = ()
    collector_contract_version: str | None = field(default=None, init=False)
    reviewer_output: ReviewerOutputState | None = field(default=None, init=False)
    role_output_state: RoleOutputState | None = field(default=None, init=False)

    @property
    def is_completed(self) -> bool:
        """Return the retained reviewer's completion state, failing for non-review roles."""

        state = self.reviewer_output or self.role_output_state
        if state is None:
            raise RuntimeError("Runtime tool context has no output state")
        return state.is_completed

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        """Expose retained completion coverage for the current Reviewer Run."""

        state = self.reviewer_output or self.role_output_state
        if state is None:
            raise RuntimeError("Runtime tool context has no output state")
        return state.incomplete_review_files

    def final_output(self) -> object:
        """Return the retained versioned output for the current Agent role."""

        state = self.reviewer_output or self.role_output_state
        if state is None:
            raise RuntimeError("Runtime tool context has no output state")
        return state.final_output()

    @classmethod
    def for_test(
        cls,
        *,
        snapshot: ReviewSnapshot,
        call_limits: ToolExecutionLimits,
    ) -> "RuntimeToolContext":
        """Build an isolated context with bounded in-memory internal-role stubs."""

        names = (
            "find_files",
            "grep",
            "read_file",
            "get_diff",
            "comment",
            "review_file_done",
            "task_done",
            "submit_review_plan",
            "submit_resolution",
            "submit_verification",
        )
        descriptions = {name: f"Test contract for {name}." for name in names}
        internal = tuple(
            RoleOutputToolBinding(
                ToolContractReference(name, 1),
                _submission_stub(name, descriptions[name]),
            )
            for name in ("submit_review_plan", "submit_resolution", "submit_verification")
        )
        return cls(
            snapshot=snapshot,
            git=GitCli(),
            tool_descriptions=descriptions,
            tool_limits=ToolLimits(),
            completion_settings=ReviewCompletionSettings(),
            call_limits=call_limits,
            role_output_tools=internal,
        )


class CapabilityToolAssembler:
    """Select ordered tools exclusively from a frozen, versioned allowlist."""

    def assemble(
        self,
        spec: FrozenAgentExecutionSpec,
        context: RuntimeToolContext,
    ) -> list[Tool]:
        """Build available adapters, select frozen contracts, then share one limiter."""

        context.collector_contract_version = None
        context.reviewer_output = None
        context.role_output_state = None
        available = self._available_tools(spec, context)
        selected: list[Tool] = []
        for reference in spec.capability_profile.builtin_tools:
            tool = available.get((reference.name, reference.version))
            if tool is None:
                raise PermanentAgentOutputError(
                    f"Tool contract is unavailable: {reference.name}:v{reference.version}",
                    phase="investigation",
                    reason_code="tool_contract_unavailable",
                    retryable=False,
                )
            selected.append(tool)
        return enforce_tool_execution_limits(
            selected,
            max_tool_calls=min(
                spec.execution_limits.max_tool_calls,
                context.call_limits.max_tool_calls,
            ),
            max_identical_tool_results=context.call_limits.max_identical_tool_results,
            tool_timeout_seconds=context.call_limits.tool_timeout_seconds,
            tool_loop_warning_template=context.call_limits.tool_loop_warning_template,
        )

    @staticmethod
    def _available_tools(
        spec: FrozenAgentExecutionSpec,
        context: RuntimeToolContext,
    ) -> dict[tuple[str, int], Tool]:
        evidence = FilesystemReviewTools(
            context.snapshot,
            context.git,
            max_tool_calls=None,
            tool_limits=context.tool_limits,
        )
        available = {
            (tool.name, 1): tool
            for tool in evidence.as_agent_tools(context.tool_descriptions)
        }
        requested = {
            (reference.name, reference.version)
            for reference in spec.capability_profile.builtin_tools
        }
        if ("comment", 1) in requested:
            if spec.agent.confidence_floor is None:
                raise PermanentAgentOutputError(
                    "Comment v1 requires a numeric confidence floor",
                    phase="investigation",
                    reason_code="legacy_confidence_floor_missing",
                    retryable=False,
                )
            legacy = ReviewCommentCollector(
                snapshot=context.snapshot,
                reviewer_id=spec.agent.agent_id,
                confidence_floor=spec.agent.confidence_floor,
                tools=evidence,
                max_incomplete_review_retries=(
                    context.completion_settings.max_incomplete_review_retries
                ),
                tool_descriptions=context.tool_descriptions,
                tool_limits=context.tool_limits,
            )
            available.update({(tool.name, 1): tool for tool in legacy.as_agent_tools()})
            context.collector_contract_version = "1"
            context.reviewer_output = ReviewerOutputState("1", legacy, legacy)
        elif ("comment", 2) in requested:
            controls = ReviewCommentCollector(
                snapshot=context.snapshot,
                reviewer_id=spec.agent.agent_id,
                confidence_floor=0.0,
                tools=evidence,
                max_incomplete_review_retries=(
                    context.completion_settings.max_incomplete_review_retries
                ),
                tool_descriptions=context.tool_descriptions,
                tool_limits=context.tool_limits,
            )
            control_tools = controls.as_agent_tools()
            for tool in control_tools:
                if tool.name != "comment":
                    available[(tool.name, 1)] = tool
            collector = ReviewCommentCollectorV2(
                task_id=context.snapshot.worktree.task_id,
                run_id=spec.fingerprint,
                snapshot=context.snapshot,
                reviewer_reference=spec.agent.reference,
                reviewer_dimensions=spec.agent.dimensions,
                tools=evidence,
                tool_limits=context.tool_limits,
            )
            available[("comment", 2)] = collector.as_comment_agent_tool(
                context.tool_descriptions["comment"]
            )
            context.collector_contract_version = "2"
            context.reviewer_output = ReviewerOutputState("2", collector, controls)
        for binding in context.role_output_tools:
            key = (binding.contract.name, binding.contract.version)
            if key in available:
                raise ValueError("duplicate built-in tool contract binding")
            available[key] = binding.tool
            if key in requested and binding.state is not None:
                if context.role_output_state is not None:
                    raise ValueError("multiple role output states were selected")
                context.role_output_state = binding.state
        return available

"""Runtime enforcement shared by model-visible function tools."""

import asyncio
import hashlib
import json
import logging
from typing import Any

from agents import FunctionTool, Tool
from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext

from codelens.review.domain.errors import (
    ToolCallLimitExceededError,
    ToolInvocationTimeoutError,
    ToolLoopDetectedError,
)
from codelens.review.domain.tool_results import (
    ToolDiagnostic,
    ToolResult,
    ToolResultError,
    ToolResultStatus,
    invalid_internal_tool_result,
    parse_tool_result,
)
from codelens.review.infrastructure.evidence_replay import (
    CompactedEvidenceReplayRegistry,
    canonicalize_tool_arguments,
)

logger = logging.getLogger(__name__)

_VOLATILE_RESULT_FIELDS = frozenset(
    {"call_id", "tool_call_id", "original_call_id", "timestamp", "created_at", "updated_at"}
)


def _rejected_result(
    tool_name: str,
    code: str,
    message: str,
    *,
    field: str | None = None,
) -> str:
    return ToolResult(
        tool_name,
        ToolResultStatus.REJECTED,
        {},
        (ToolDiagnostic(code, message, True, field),),
    ).to_json()


def ensure_tool_result(tool_name: str, result: object) -> str:
    """Return a valid result for this tool or a safe structured contract failure."""

    if isinstance(result, str):
        try:
            parsed = parse_tool_result(result)
        except ToolResultError:
            parsed = None
        if parsed is not None and parsed.tool == tool_name:
            return parsed.to_json()
    logger.error(
        "model-visible tool returned an invalid internal result",
        extra={"tool_name": tool_name, "reason_code": "invalid_internal_tool_result"},
    )
    return invalid_internal_tool_result(
        tool_name,
        "The tool returned a result that violates its internal contract.",
    ).to_json()


def reject_unknown_arguments(tool: FunctionTool) -> FunctionTool:
    """Convert argument and adapter failures to the shared Tool Result v2 contract."""

    invoke = tool.on_invoke_tool
    expected = frozenset(tool.params_json_schema.get("properties", {}))

    async def invoke_strict(context: ToolContext[Any], arguments: str) -> Any:
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return _rejected_result(
                tool.name,
                "invalid_arguments_json",
                "Tool arguments must be one valid JSON object.",
            )
        if not isinstance(parsed, dict):
            return _rejected_result(
                tool.name,
                "invalid_argument_type",
                "Tool arguments must be a JSON object.",
            )
        unknown = sorted(str(name) for name in parsed if name not in expected)
        if unknown:
            return _rejected_result(
                tool.name,
                "unknown_argument",
                "Tool arguments contain an unsupported field.",
                field=unknown[0],
            )
        try:
            result = await invoke(context, arguments)
        except ModelBehaviorError as error:
            error_text = str(error)
            code = (
                "invalid_argument_type"
                if any(
                    marker in error_text for marker in ("_type", "Input should be a valid", "type=")
                )
                else "invalid_argument_value"
            )
            return _rejected_result(
                tool.name,
                code,
                f"Tool arguments failed schema validation: {error_text}",
            )
        except ValueError:
            return _rejected_result(
                tool.name,
                "invalid_argument_value",
                "Tool arguments violate an input constraint.",
            )
        if isinstance(result, str):
            try:
                parsed_result = parse_tool_result(result)
            except ToolResultError:
                parsed_result = None
            if parsed_result is not None:
                if parsed_result.tool == tool.name:
                    return parsed_result.to_json()
                return ensure_tool_result(tool.name, result)
            sdk_validation_prefix = f"Invalid JSON input for tool {tool.name}:"
            if sdk_validation_prefix in result:
                code = (
                    "invalid_argument_type"
                    if any(
                        marker in result
                        for marker in ("_type", "Input should be a valid", "type=")
                    )
                    else "invalid_argument_value"
                )
                validation_detail = result.removeprefix(
                    sdk_validation_prefix
                ).strip()
                return _rejected_result(
                    tool.name,
                    code,
                    f"Tool arguments failed schema validation: {validation_detail}",
                )
        return ensure_tool_result(tool.name, result)

    tool.on_invoke_tool = invoke_strict
    return tool


_EVIDENCE_TOOL_NAMES = frozenset(
    (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
    )
)
"""Read-only tools whose effects must become visible before state mutations."""


class ToolBatchPhaseCoordinator:
    """Schedule read-only evidence ahead of state changes in one SDK tool batch.

    The Agents SDK starts every function call in a turn as its own task.  Tool
    wrappers do not receive the batch list before those tasks start, so the
    coordinator uses a short admission interval: every invocation first registers,
    then the batch closes.  Evidence proceeds once admission closes, while state
    and completion tools additionally wait for all registered evidence calls to
    succeed, fail, or be cancelled.  The interval is deliberately tiny and the
    runtime explicitly preserves the SDK default of launching the complete batch.
    """

    _ADMISSION_INTERVAL_SECONDS = 0.01

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_calls: set[str] = set()
        self._active_evidence: set[str] = set()
        self._admission_closing = False
        self._admission_closed = False

    def wrap(self, tool: FunctionTool) -> FunctionTool:
        """Attach phase scheduling to one function tool."""

        invoke = tool.on_invoke_tool
        is_evidence = tool.name in _EVIDENCE_TOOL_NAMES

        async def invoke_phased(context: ToolContext[Any], arguments: str) -> Any:
            call_id = context.tool_call_id or f"{tool.name}:{id(invoke)}"
            try:
                await self._register(call_id, is_evidence)
                return await invoke(context, arguments)
            finally:
                await self._finish(call_id, is_evidence)

        tool.on_invoke_tool = invoke_phased
        return tool

    async def _register(self, call_id: str, is_evidence: bool) -> None:
        async with self._condition:
            if call_id in self._active_calls:
                raise RuntimeError(f"duplicate tool call id: {call_id}")
            self._active_calls.add(call_id)
            if is_evidence:
                self._active_evidence.add(call_id)
            if not self._admission_closing:
                self._admission_closing = True
                asyncio.create_task(self._close_after_admission())
            await self._condition.wait_for(lambda: self._admission_closed)
            if is_evidence:
                return
            await self._condition.wait_for(lambda: not self._active_evidence)

    async def _close_after_admission(self) -> None:
        try:
            await asyncio.sleep(self._ADMISSION_INTERVAL_SECONDS)
            async with self._condition:
                self._admission_closed = True
                self._condition.notify_all()
        except BaseException:
            async with self._condition:
                self._admission_closed = True
                self._condition.notify_all()
            raise

    async def _finish(self, call_id: str, is_evidence: bool) -> None:
        async with self._condition:
            self._active_calls.remove(call_id)
            if is_evidence:
                self._active_evidence.remove(call_id)
            self._condition.notify_all()
            if not self._active_calls:
                self._admission_closed = False
                self._admission_closing = False


class ToolExecutionLimiter:
    """Enforce one Agent run's call budget, deadline, and no-progress loop breaker.

    Only hashes of normalized arguments and results are retained in memory. Reaching
    a configured bound raises a provider-neutral failure so the model cannot spend
    the remainder of its turns repeating an operation that returned no new evidence.
    """

    def __init__(
        self,
        *,
        max_tool_calls: int,
        max_identical_tool_results: int,
        tool_timeout_seconds: float,
        tool_loop_warning_template: str,
        evidence_replay_registry: CompactedEvidenceReplayRegistry | None = None,
    ) -> None:
        if max_tool_calls <= 0:
            raise ValueError("tool call budget must be positive")
        if max_identical_tool_results < 2:
            raise ValueError("identical tool result limit must be at least two")
        if tool_timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        self._remaining_calls = max_tool_calls
        self._max_identical_tool_results = max_identical_tool_results
        self._tool_timeout_seconds = tool_timeout_seconds
        self._tool_loop_warning_template = tool_loop_warning_template
        self._evidence_replay_registry = evidence_replay_registry
        self._last_fingerprint: str | None = None
        self._consecutive_identical_count = 0
        self._lock = asyncio.Lock()

    def wrap(self, tool: FunctionTool) -> FunctionTool:
        """Attach this run-scoped limiter to one function tool."""

        invoke = tool.on_invoke_tool

        async def invoke_limited(context: ToolContext[Any], arguments: str) -> Any:
            await self._consume_call()
            try:
                async with asyncio.timeout(self._tool_timeout_seconds):
                    result = await invoke(context, arguments)
            except TimeoutError:
                raise ToolInvocationTimeoutError(
                    "A model-visible tool exceeded its configured timeout.",
                    phase="investigation",
                    reason_code="tool_invocation_timed_out",
                    retryable=False,
                ) from None
            normalized_result = ensure_tool_result(tool.name, result)
            warning = await self._observe_result(tool.name, arguments, normalized_result)
            if warning is not None:
                return self._attach_warning(normalized_result, warning)
            return normalized_result

        tool.on_invoke_tool = invoke_limited
        return tool

    async def _consume_call(self) -> None:
        async with self._lock:
            if self._remaining_calls <= 0:
                raise ToolCallLimitExceededError(
                    "The model used all allowed tool calls.",
                    phase="investigation",
                    reason_code="max_tool_calls_exceeded",
                    retryable=False,
                )
            self._remaining_calls -= 1

    async def _observe_result(self, tool_name: str, arguments: str, result: object) -> str | None:
        """Check for repeated tool calls and return a warning if detected.

        Returns a warning message on first detection of repetition (count == 2),
        or raises ToolLoopDetectedError if the threshold is reached.
        """
        if (
            self._evidence_replay_registry is not None
            and isinstance(result, str)
            and parse_tool_result(result).status
            in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}
            and self._evidence_replay_registry.consume(tool_name, arguments)
        ):
            async with self._lock:
                self._last_fingerprint = None
                self._consecutive_identical_count = 0
            return None
        fingerprint = self._fingerprint(tool_name, arguments, result)
        async with self._lock:
            if fingerprint == self._last_fingerprint:
                self._consecutive_identical_count += 1
            else:
                self._last_fingerprint = fingerprint
                self._consecutive_identical_count = 1
            repeated_count = self._consecutive_identical_count

            # Threshold reached: fail the run
            if repeated_count >= self._max_identical_tool_results:
                raise ToolLoopDetectedError(
                    "The model repeated an identical tool call without making progress.",
                    phase="investigation",
                    reason_code="identical_tool_result_loop",
                    retryable=False,
                )

            # Repetition detected but below threshold: warn the model
            if repeated_count >= 2:
                remaining = self._max_identical_tool_results - repeated_count
                return self._tool_loop_warning_template.format(
                    repeated_count=repeated_count,
                    remaining=remaining,
                )

            return None

    @staticmethod
    def _attach_warning(result: object, warning: str) -> str:
        """Append a stable Diagnostic through the canonical Tool Result serializer."""

        if not isinstance(result, str):
            raise ToolResultError("limiter requires a serialized Tool Result")
        parsed = parse_tool_result(result)
        return parsed.with_diagnostic(
            ToolDiagnostic(
                "repeated_identical_call",
                warning,
                True,
            )
        ).to_json()

    @classmethod
    def _fingerprint(cls, tool_name: str, arguments: str, result: object) -> str:
        normalized_arguments = cls._normalize_json(arguments)
        normalized_result = (
            cls._normalize_result_for_fingerprint(result)
            if isinstance(result, str)
            else str(result)
        )
        payload = json.dumps(
            [tool_name, normalized_arguments, normalized_result],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _normalize_json(value: str) -> str:
        return canonicalize_tool_arguments(value)

    @staticmethod
    def _normalize_result_for_fingerprint(result: str) -> str:
        """Canonicalize semantic result state while dropping localized or volatile fields."""

        try:
            parsed = parse_tool_result(result)
        except ToolResultError:
            return canonicalize_tool_arguments(result)

        def stable_value(value: object) -> object:
            if isinstance(value, dict):
                return {
                    key: stable_value(item)
                    for key, item in value.items()
                    if key not in _VOLATILE_RESULT_FIELDS
                }
            if isinstance(value, list):
                return [stable_value(item) for item in value]
            return value

        diagnostics = [
            {
                "code": diagnostic.code,
                "retryable": diagnostic.retryable,
                **({"field": diagnostic.field} if diagnostic.field is not None else {}),
                **(
                    {"suggested_arguments": stable_value(diagnostic.suggested_arguments)}
                    if diagnostic.suggested_arguments is not None
                    else {}
                ),
            }
            for diagnostic in parsed.diagnostics
            if diagnostic.code != "repeated_identical_call"
        ]
        return json.dumps(
            {
                "schema_version": parsed.schema_version,
                "tool": parsed.tool,
                "status": parsed.status.value,
                "data": stable_value(parsed.data),
                "diagnostics": diagnostics,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def enforce_tool_execution_limits(
    tools: list[Tool],
    *,
    max_tool_calls: int,
    max_identical_tool_results: int,
    tool_timeout_seconds: float,
    tool_loop_warning_template: str,
    evidence_replay_registry: CompactedEvidenceReplayRegistry | None = None,
) -> list[Tool]:
    """Apply one shared execution limiter to every function tool in an Agent run."""

    limiter = ToolExecutionLimiter(
        max_tool_calls=max_tool_calls,
        max_identical_tool_results=max_identical_tool_results,
        tool_timeout_seconds=tool_timeout_seconds,
        tool_loop_warning_template=tool_loop_warning_template,
        evidence_replay_registry=evidence_replay_registry,
    )
    coordinator = ToolBatchPhaseCoordinator()
    limited: list[Tool] = []
    for tool in tools:
        if not isinstance(tool, FunctionTool):
            raise TypeError("execution limits require function tools")
        limited.append(limiter.wrap(coordinator.wrap(tool)))
    return limited

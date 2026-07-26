"""Runtime enforcement shared by model-visible function tools."""

import asyncio
import hashlib
import json
from typing import Any

from agents import FunctionTool, Tool
from agents.tool_context import ToolContext

from codelens.review.domain.errors import (
    ToolCallLimitExceededError,
    ToolInvocationTimeoutError,
    ToolLoopDetectedError,
)


def reject_unknown_arguments(tool: FunctionTool) -> FunctionTool:
    """Reject fields forbidden by the advertised strict schema at the local boundary."""

    invoke = tool.on_invoke_tool
    expected = frozenset(tool.params_json_schema.get("properties", {}))

    async def invoke_strict(context: ToolContext[Any], arguments: str) -> Any:
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return await invoke(context, arguments)
        if isinstance(parsed, dict):
            unknown = sorted(str(name) for name in parsed if name not in expected)
            if unknown:
                fields = ", ".join(unknown)
                return f"Tool arguments contain unsupported fields: {fields}"
        return await invoke(context, arguments)

    tool.on_invoke_tool = invoke_strict
    return tool


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
        self._result_counts: dict[str, int] = {}
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
            await self._observe_result(tool.name, arguments, result)
            return result

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

    async def _observe_result(self, tool_name: str, arguments: str, result: object) -> None:
        fingerprint = self._fingerprint(tool_name, arguments, result)
        async with self._lock:
            repeated_count = self._result_counts.get(fingerprint, 0) + 1
            self._result_counts[fingerprint] = repeated_count
            if repeated_count >= self._max_identical_tool_results:
                raise ToolLoopDetectedError(
                    "The model repeated an identical tool call without making progress.",
                    phase="investigation",
                    reason_code="identical_tool_result_loop",
                    retryable=False,
                )

    @classmethod
    def _fingerprint(cls, tool_name: str, arguments: str, result: object) -> str:
        normalized_arguments = cls._normalize_json(arguments)
        normalized_result = cls._normalize_json(result) if isinstance(result, str) else str(result)
        payload = json.dumps(
            [tool_name, normalized_arguments, normalized_result],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _normalize_json(value: str) -> str:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def enforce_tool_execution_limits(
    tools: list[Tool],
    *,
    max_tool_calls: int,
    max_identical_tool_results: int,
    tool_timeout_seconds: float,
) -> list[Tool]:
    """Apply one shared execution limiter to every function tool in an Agent run."""

    limiter = ToolExecutionLimiter(
        max_tool_calls=max_tool_calls,
        max_identical_tool_results=max_identical_tool_results,
        tool_timeout_seconds=tool_timeout_seconds,
    )
    limited: list[Tool] = []
    for tool in tools:
        if not isinstance(tool, FunctionTool):
            raise TypeError("execution limits require function tools")
        limited.append(limiter.wrap(tool))
    return limited

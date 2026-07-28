import asyncio
import json

import pytest
from agents import RunConfig, Usage, function_tool
from agents.tool_context import ToolContext

from codelens.review.domain.errors import (
    ToolCallLimitExceededError,
    ToolInvocationTimeoutError,
    ToolLoopDetectedError,
)
from codelens.review.infrastructure.tool_contract import enforce_tool_execution_limits


def _context(tool_name: str, arguments: str) -> ToolContext[None]:
    return ToolContext(
        None,
        usage=Usage(),
        tool_name=tool_name,
        tool_call_id=f"call-{tool_name}",
        tool_arguments=arguments,
        run_config=RunConfig(),
    )


async def test_repeated_identical_arguments_and_results_trip_the_loop_breaker() -> None:
    @function_tool
    async def stable_lookup(query: str) -> str:
        return json.dumps({"query": query, "matches": []}, sort_keys=True)

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]
    arguments = json.dumps({"query": "missingSymbol"})

    await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_repeated_identical_call_returns_warning_before_failure() -> None:
    @function_tool
    async def stable_lookup(query: str) -> str:
        return json.dumps({"query": query, "matches": []}, sort_keys=True)

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]
    arguments = json.dumps({"query": "missingSymbol"})

    # First call: no warning
    first_result = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" not in first_result

    # Second call: warning attached
    second_result = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in second_result
    assert "2 times" in second_result
    assert "1 attempt(s) left" in second_result

    # Third call: still raises
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_changed_arguments_or_results_make_progress_for_loop_detection() -> None:
    call_count = 0

    @function_tool
    async def changing_lookup(query: str) -> str:
        nonlocal call_count
        call_count += 1
        return json.dumps({"query": query, "attempt": call_count})

    tool = enforce_tool_execution_limits(
        [changing_lookup],
        max_tool_calls=20,
        max_identical_tool_results=2,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]

    for query in ("one", "one", "two", "one"):
        arguments = json.dumps({"query": query})
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_repeated_identical_calls_return_warning_before_failure() -> None:
    """Verify that repeated identical calls return a warning before raising an exception."""

    @function_tool
    async def stable_lookup(query: str) -> str:
        return json.dumps({"query": query, "matches": []}, sort_keys=True)

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]
    arguments = json.dumps({"query": "missingSymbol"})

    # First call: normal result, no warning
    result1 = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" not in result1
    assert "missingSymbol" in result1

    # Second call: result with warning
    result2 = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in result2
    assert "2 times" in result2
    assert "missingSymbol" in result2

    # Third call: exception
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_warning_includes_remaining_attempts() -> None:
    """Verify that the warning message includes the number of remaining attempts."""

    @function_tool
    async def stable_lookup(query: str) -> str:
        return json.dumps({"query": query, "matches": []}, sort_keys=True)

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=4,  # Higher threshold
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]
    arguments = json.dumps({"query": "test"})

    # First call: no warning
    result1 = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" not in result1

    # Second call: warning with 2 remaining attempts
    result2 = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in result2
    assert "2 times" in result2
    assert "2 attempt(s) left" in result2

    # Third call: warning with 1 remaining attempt
    result3 = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in result3
    assert "3 times" in result3
    assert "1 attempt(s) left" in result3

    # Fourth call: exception
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_tool_call_budget_and_timeout_fail_the_run() -> None:
    @function_tool
    async def slow_lookup(query: str) -> str:
        await asyncio.sleep(0.05)
        return query

    budgeted = enforce_tool_execution_limits(
        [slow_lookup],
        max_tool_calls=1,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]
    arguments = json.dumps({"query": "value"})
    await budgeted.on_invoke_tool(_context(budgeted.name, arguments), arguments)
    with pytest.raises(ToolCallLimitExceededError):
        await budgeted.on_invoke_tool(_context(budgeted.name, arguments), arguments)

    @function_tool
    async def timed_lookup(query: str) -> str:
        await asyncio.sleep(0.05)
        return query

    timed = enforce_tool_execution_limits(
        [timed_lookup],
        max_tool_calls=2,
        max_identical_tool_results=3,
        tool_timeout_seconds=0.01,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]
    with pytest.raises(ToolInvocationTimeoutError):
        await timed.on_invoke_tool(_context(timed.name, arguments), arguments)

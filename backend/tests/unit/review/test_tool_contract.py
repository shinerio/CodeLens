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
from codelens.review.domain.tool_results import (
    ToolDiagnostic,
    ToolResult,
    ToolResultStatus,
)
from codelens.review.infrastructure.evidence_replay import (
    CompactedEvidenceReplayRegistry,
)
from codelens.review.infrastructure.tool_contract import (
    enforce_tool_execution_limits,
    reject_unknown_arguments,
)


def _success(tool_name: str, data: dict[str, object]) -> str:
    return ToolResult(tool_name, ToolResultStatus.SUCCESS, data).to_json()


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
        return _success("stable_lookup", {"query": query, "matches": []})

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
        return _success("stable_lookup", {"query": query, "matches": []})

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
    second_payload = json.loads(second_result)
    assert second_payload["status"] == "success"
    assert second_payload["diagnostics"][0]["code"] == "repeated_identical_call"
    assert "2 times" in second_payload["diagnostics"][0]["message"]
    assert "1 attempt(s) left" in second_payload["diagnostics"][0]["message"]

    # Third call: still raises
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_changed_arguments_or_results_make_progress_for_loop_detection() -> None:
    call_count = 0

    @function_tool
    async def changing_lookup(query: str) -> str:
        nonlocal call_count
        call_count += 1
        return _success("changing_lookup", {"query": query, "attempt": call_count})

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
        return _success("stable_lookup", {"query": query, "matches": []})

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
    result2_payload = json.loads(result2)
    assert result2_payload["diagnostics"][0]["code"] == "repeated_identical_call"
    assert "2 times" in result2_payload["diagnostics"][0]["message"]
    assert result2_payload["data"]["query"] == "missingSymbol"

    # Third call: exception
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_warning_includes_remaining_attempts() -> None:
    """Verify that the warning message includes the number of remaining attempts."""

    @function_tool
    async def stable_lookup(query: str) -> str:
        return _success("stable_lookup", {"query": query, "matches": []})

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
    result2_payload = json.loads(result2)
    assert "2 times" in result2_payload["diagnostics"][0]["message"]
    assert "2 attempt(s) left" in result2_payload["diagnostics"][0]["message"]

    # Third call: warning with 1 remaining attempt
    result3 = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    result3_payload = json.loads(result3)
    assert "3 times" in result3_payload["diagnostics"][0]["message"]
    assert "1 attempt(s) left" in result3_payload["diagnostics"][0]["message"]

    # Fourth call: exception
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_tool_call_budget_and_timeout_fail_the_run() -> None:
    @function_tool
    async def slow_lookup(query: str) -> str:
        await asyncio.sleep(0.05)
        return _success("slow_lookup", {"query": query})

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
        return _success("timed_lookup", {"query": query})

    timed = enforce_tool_execution_limits(
        [timed_lookup],
        max_tool_calls=2,
        max_identical_tool_results=3,
        tool_timeout_seconds=0.01,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]
    with pytest.raises(ToolInvocationTimeoutError):
        await timed.on_invoke_tool(_context(timed.name, arguments), arguments)


async def test_argument_boundary_returns_structured_rejections() -> None:
    @function_tool
    async def lookup(query: str) -> str:
        return _success("lookup", {"query": query})

    tool = reject_unknown_arguments(lookup)

    malformed = json.loads(await tool.on_invoke_tool(_context(tool.name, "{"), "{"))
    unknown_arguments = json.dumps({"query": "x", "extra": True})
    unknown = json.loads(
        await tool.on_invoke_tool(_context(tool.name, unknown_arguments), unknown_arguments)
    )
    invalid_type_arguments = json.dumps({"query": 7})
    invalid_type = json.loads(
        await tool.on_invoke_tool(
            _context(tool.name, invalid_type_arguments), invalid_type_arguments
        )
    )

    assert malformed["status"] == "rejected"
    assert malformed["diagnostics"][0]["code"] == "invalid_arguments_json"
    assert unknown["diagnostics"][0] == {
        "code": "unknown_argument",
        "field": "extra",
        "message": "Tool arguments contain an unsupported field.",
        "retryable": True,
    }
    assert invalid_type["diagnostics"][0]["code"] == "invalid_argument_type"


async def test_invalid_internal_result_is_replaced_without_exposing_it() -> None:
    @function_tool
    async def lookup(query: str) -> str:
        return f"sensitive legacy result for {query}"

    tool = reject_unknown_arguments(lookup)
    arguments = json.dumps({"query": "secret"})

    result = json.loads(await tool.on_invoke_tool(_context(tool.name, arguments), arguments))

    assert result["status"] == "failed"
    assert result["data"] == {}
    assert result["diagnostics"][0]["code"] == "invalid_internal_tool_result"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "source_text",
    (
        "validation error for ExampleModel",
        "Invalid JSON input for tool example",
        "type=string should remain repository evidence",
    ),
)
async def test_valid_tool_result_content_is_never_reclassified_as_argument_error(
    source_text: str,
) -> None:
    @function_tool(name_override="read_file")
    async def read_file_tool(path: str) -> str:
        return _success("read_file", {"path": path, "content": source_text})

    tool = reject_unknown_arguments(read_file_tool)
    arguments = json.dumps({"path": "src/example.py"})

    result = json.loads(
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    )

    assert result["status"] == "success"
    assert result["data"]["content"] == source_text


async def test_non_consecutive_a_b_a_calls_reset_the_streak() -> None:
    @function_tool
    async def stable_lookup(query: str) -> str:
        return _success("stable_lookup", {"result": "same"})

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=3,
        max_identical_tool_results=2,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
    )[0]

    for query in ("A", "B", "A"):
        arguments = json.dumps({"query": query})
        result = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
        assert json.loads(result)["diagnostics"] == []


async def test_canonical_json_key_order_counts_as_the_same_call() -> None:
    @function_tool
    async def stable_lookup(query: str, scope: str) -> str:
        return _success("stable_lookup", {"scope": scope, "query": query})

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=3,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
    )[0]
    first_arguments = '{"query":"A","scope":"src"}'
    second_arguments = '{"scope":"src","query":"A"}'

    await tool.on_invoke_tool(_context(tool.name, first_arguments), first_arguments)
    second = await tool.on_invoke_tool(_context(tool.name, second_arguments), second_arguments)

    assert json.loads(second)["diagnostics"][0]["code"] == "repeated_identical_call"


async def test_exact_compacted_evidence_reread_consumes_allowance_and_resets_streak() -> None:
    registry = CompactedEvidenceReplayRegistry()

    @function_tool(name_override="get_diff")
    async def get_diff_tool(path: str) -> str:
        return _success("get_diff", {"path": path})

    tool = enforce_tool_execution_limits(
        [get_diff_tool],
        max_tool_calls=5,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
        evidence_replay_registry=registry,
    )[0]
    arguments = '{"path":"src"}'

    await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert registry.register("original-call", "get_diff", arguments)
    reread = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    after_reread = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    warning = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)

    assert json.loads(reread)["diagnostics"] == []
    assert json.loads(after_reread)["diagnostics"] == []
    assert json.loads(warning)["diagnostics"][0]["code"] == "repeated_identical_call"
    assert registry.consumed_count == 1
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)


async def test_localized_messages_and_volatile_ids_do_not_change_repeat_fingerprint() -> None:
    call_count = 0

    @function_tool
    async def localized_lookup(query: str) -> str:
        nonlocal call_count
        call_count += 1
        message = "No matches." if call_count == 1 else "没有匹配项。"
        return ToolResult(
            "localized_lookup",
            ToolResultStatus.SUCCESS,
            {
                "query": query,
                "call_id": f"call-{call_count}",
                "timestamp": f"2026-08-14T00:00:0{call_count}Z",
            },
            (ToolDiagnostic("no_matches", message, True),),
        ).to_json()

    tool = enforce_tool_execution_limits(
        [localized_lookup],
        max_tool_calls=3,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
    )[0]
    arguments = '{"query":"symbol"}'

    await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    repeated = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)

    assert json.loads(repeated)["diagnostics"][-1]["code"] == "repeated_identical_call"


async def test_compaction_allowance_is_consumed_only_after_accepted_evidence() -> None:
    registry = CompactedEvidenceReplayRegistry()
    call_count = 0

    @function_tool(name_override="read_file")
    async def read_file_tool(path: str) -> str:
        nonlocal call_count
        call_count += 1
        status = ToolResultStatus.REJECTED if call_count == 1 else ToolResultStatus.SUCCESS
        return ToolResult("read_file", status, {"path": path}).to_json()

    arguments = '{"path":"src/a.py"}'
    assert registry.register("compressed-call", "read_file", arguments)
    tool = enforce_tool_execution_limits(
        [read_file_tool],
        max_tool_calls=2,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
        evidence_replay_registry=registry,
    )[0]

    rejected = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert json.loads(rejected)["status"] == "rejected"
    assert registry.consumed_count == 0

    accepted = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert registry.consumed_count == 1
    assert json.loads(accepted)["status"] == "success"


async def test_compaction_replay_still_consumes_the_total_call_budget() -> None:
    registry = CompactedEvidenceReplayRegistry()

    @function_tool(name_override="get_diff")
    async def get_diff_tool(path: str) -> str:
        return _success("get_diff", {"path": path})

    arguments = '{"path":"src"}'
    assert registry.register("compressed-call", "get_diff", arguments)
    tool = enforce_tool_execution_limits(
        [get_diff_tool],
        max_tool_calls=1,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
        evidence_replay_registry=registry,
    )[0]

    await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert registry.consumed_count == 1
    with pytest.raises(ToolCallLimitExceededError):
        await tool.on_invoke_tool(_context(tool.name, arguments), arguments)

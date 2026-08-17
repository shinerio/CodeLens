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
    ToolLoopResetSignal,
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


async def test_non_consecutive_a_b_a_calls_detected_as_repetition() -> None:
    """A B A pattern: the second A must be detected as a repetition."""

    @function_tool
    async def stable_lookup(query: str) -> str:
        return _success("stable_lookup", {"result": "same"})

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
    )[0]

    # A: first call, no warning
    arguments_a = json.dumps({"query": "A"})
    first = await tool.on_invoke_tool(_context(tool.name, arguments_a), arguments_a)
    assert json.loads(first)["diagnostics"] == []

    # B: different fingerprint, no warning
    arguments_b = json.dumps({"query": "B"})
    second = await tool.on_invoke_tool(_context(tool.name, arguments_b), arguments_b)
    assert json.loads(second)["diagnostics"] == []

    # A: same fingerprint as first call → repeated_count=2 → warning
    third = await tool.on_invoke_tool(_context(tool.name, arguments_a), arguments_a)
    third_payload = json.loads(third)
    assert third_payload["diagnostics"][0]["code"] == "repeated_identical_call"
    assert "2" in third_payload["diagnostics"][0]["message"]


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


async def test_mixed_batch_runs_evidence_before_state_and_completion_tools() -> None:
    evidence_started = asyncio.Event()
    release_evidence = asyncio.Event()
    state_entered = False

    @function_tool(name_override="read_file")
    async def read_file(path: str) -> str:
        evidence_started.set()
        await release_evidence.wait()
        return _success("read_file", {"path": path, "content": "trusted evidence"})

    @function_tool(name_override="task_done")
    async def task_done(summary: str) -> str:
        nonlocal state_entered
        state_entered = True
        return _success("task_done", {"summary": summary})

    tools = enforce_tool_execution_limits(
        [task_done, read_file],
        max_tool_calls=2,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )
    completion = tools[0]
    evidence = tools[1]
    completion_arguments = json.dumps({"summary": "complete"})
    evidence_arguments = json.dumps({"path": "src/example.py"})
    completion_task = asyncio.create_task(
        completion.on_invoke_tool(
            _context(completion.name, completion_arguments), completion_arguments
        )
    )
    evidence_task = asyncio.create_task(
        evidence.on_invoke_tool(
            _context(evidence.name, evidence_arguments), evidence_arguments
        )
    )

    await asyncio.wait_for(evidence_started.wait(), timeout=0.5)
    assert state_entered is False

    release_evidence.set()
    completion_result, evidence_result = await asyncio.gather(completion_task, evidence_task)

    assert json.loads(completion_result)["status"] == "success"
    assert json.loads(evidence_result)["status"] == "success"
    assert state_entered is True


async def test_failed_evidence_still_releases_state_phase_waiters() -> None:
    evidence_started = asyncio.Event()
    state_result: dict[str, object] | None = None

    @function_tool(name_override="grep")
    async def grep(pattern: str) -> str:
        evidence_started.set()
        raise ValueError("evidence adapter failed")

    @function_tool(name_override="comment")
    async def comment(path: str, line: int, body: str) -> str:
        nonlocal state_result
        state_result = {"path": path, "line": line, "body": body}
        return _success("comment", state_result)

    tools = enforce_tool_execution_limits(
        [comment, grep],
        max_tool_calls=2,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )
    completion = tools[0]
    evidence = tools[1]
    completion_arguments = json.dumps({"path": "src/example.py", "line": 1, "body": "Finding"})
    evidence_arguments = json.dumps({"pattern": "example"})
    completion_task = asyncio.create_task(
        completion.on_invoke_tool(
            _context(completion.name, completion_arguments), completion_arguments
        )
    )
    evidence_task = asyncio.create_task(
        evidence.on_invoke_tool(
            _context(evidence.name, evidence_arguments), evidence_arguments
        )
    )

    await asyncio.wait_for(evidence_started.wait(), timeout=0.5)
    evidence_result = await evidence_task
    completion_result = await completion_task

    assert json.loads(evidence_result)["status"] == "failed"
    assert state_result is not None
    assert json.loads(completion_result)["status"] == "success"


async def test_loop_reset_signal_clears_duplicate_count_after_compaction() -> None:
    """A generation change from ToolLoopResetSignal must reset the loop counters."""

    @function_tool
    async def stable_lookup(query: str) -> str:
        return _success("stable_lookup", {"query": query, "matches": []})

    signal = ToolLoopResetSignal()
    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
        loop_reset_signal=signal,
    )[0]
    arguments = json.dumps({"query": "missingSymbol"})

    # Two identical calls build up the counter to 2 (warning territory)
    first = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" not in first
    second = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in second

    # Trigger compaction reset
    signal.trigger()

    # The same call must not be flagged — counter was reset
    third = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" not in third

    # Without another reset, the next identical call warns again
    fourth = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in fourth


async def test_read_file_chunked_ranges_do_not_trigger_loop_detection() -> None:
    """Different start_line/end_line arguments produce different fingerprints."""

    @function_tool(name_override="read_file")
    async def read_file(path: str, start_line: int, end_line: int) -> str:
        return _success("read_file", {"path": path, "lines": list(range(start_line, end_line + 1))})

    tool = enforce_tool_execution_limits(
        [read_file],
        max_tool_calls=20,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
    )[0]

    for start, end in [(1, 100), (101, 200), (201, 300)]:
        arguments = json.dumps({"path": "src/example.py", "start_line": start, "end_line": end})
        result = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
        assert "WARNING" not in result


async def test_a_b_a_pattern_trips_breaker_at_threshold() -> None:
    """A B A with max=2: second A reaches threshold and trips the breaker."""

    @function_tool
    async def stable_lookup(query: str) -> str:
        return _success("stable_lookup", {"result": "same"})

    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=2,
        tool_timeout_seconds=1,
        tool_loop_warning_template="Repeated {repeated_count}; {remaining} remain.",
    )[0]

    arguments_a = json.dumps({"query": "A"})
    arguments_b = json.dumps({"query": "B"})

    # A: count=1, no warning
    await tool.on_invoke_tool(_context(tool.name, arguments_a), arguments_a)
    # B: different fingerprint, count=1
    await tool.on_invoke_tool(_context(tool.name, arguments_b), arguments_b)
    # A: same as first → count=2 → 2>=2 → ToolLoopDetectedError
    with pytest.raises(ToolLoopDetectedError):
        await tool.on_invoke_tool(_context(tool.name, arguments_a), arguments_a)


async def test_window_cleared_after_loop_reset_signal() -> None:
    """After compaction reset, the fingerprint window is cleared."""

    @function_tool
    async def stable_lookup(query: str) -> str:
        return _success("stable_lookup", {"query": query, "matches": []})

    signal = ToolLoopResetSignal()
    tool = enforce_tool_execution_limits(
        [stable_lookup],
        max_tool_calls=20,
        max_identical_tool_results=3,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} attempt(s) left",
        loop_reset_signal=signal,
    )[0]
    arguments = json.dumps({"query": "test"})

    # Build up to warning
    await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    second = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in second

    # Reset
    signal.trigger()

    # Window cleared — first two calls after reset should not warn
    first_after = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" not in first_after
    second_after = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    assert "WARNING" in second_after


# ---------------------------------------------------------------------------
# No-progress rounds nudge
# ---------------------------------------------------------------------------


class _CoverageProbe:
    """Minimal ReviewCoverageProbe implementation for testing."""

    def __init__(self, files: tuple[str, ...], reviewed: set[str] | None = None) -> None:
        self._files = files
        self._reviewed = reviewed or set()

    @property
    def review_file_paths(self) -> tuple[str, ...]:
        return self._files

    @property
    def reviewed_paths(self) -> frozenset[str]:
        return frozenset(self._reviewed)

    def mark_reviewed(self, path: str) -> None:
        self._reviewed.add(path)


async def test_no_progress_nudge_emitted_after_threshold_evidence_calls() -> None:
    @function_tool
    async def read_file(path: str) -> str:
        return _success("read_file", {"path": path, "lines": []})

    tool = enforce_tool_execution_limits(
        [read_file],
        max_tool_calls=50,
        max_identical_tool_results=99,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} left",
        no_progress_rounds_threshold=3,
        no_progress_nudge_template="No progress for {no_progress_rounds} rounds.",
    )[0]

    arguments = json.dumps({"path": "src/a.py"})

    # 2 calls below threshold — no nudge
    for _ in range(2):
        result = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
        assert "no_progress_rounds" not in result

    # 3rd call reaches threshold — nudge attached
    result = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
    payload = json.loads(result)
    nudge_diags = [d for d in payload["diagnostics"] if d["code"] == "no_progress_rounds"]
    assert len(nudge_diags) == 1
    assert "No progress for 3 rounds." in nudge_diags[0]["message"]


async def test_no_progress_nudge_resets_on_output_tool() -> None:
    @function_tool
    async def read_file(path: str) -> str:
        return _success("read_file", {"path": path, "lines": []})

    @function_tool
    async def comment(content: str) -> str:
        return _success("comment", {"comment_id": "c1"})

    tools = enforce_tool_execution_limits(
        [read_file, comment],
        max_tool_calls=50,
        max_identical_tool_results=99,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count} times, {remaining} left",
        no_progress_rounds_threshold=3,
        no_progress_nudge_template="No progress for {no_progress_rounds} rounds.",
    )
    reader = tools[0]
    commenter = tools[1]

    # Use different paths to avoid loop detection on identical calls
    for i in range(3):
        args = json.dumps({"path": f"src/file_{i}.py"})
        result = await reader.on_invoke_tool(_context(reader.name, args), args)
        if i < 2:
            assert "no_progress_rounds" not in result
        else:
            assert "no_progress_rounds" in result

    # Call comment — should reset the counter
    comment_args = json.dumps({"content": "test finding"})
    comment_result = await commenter.on_invoke_tool(
        _context(commenter.name, comment_args), comment_args
    )
    assert "no_progress_rounds" not in comment_result

    # Next read call should start from 0 again — 1 round, below threshold
    read_args = json.dumps({"path": "src/next.py"})
    result = await reader.on_invoke_tool(_context(reader.name, read_args), read_args)
    assert "no_progress_rounds" not in result


async def test_no_progress_disabled_when_threshold_is_none() -> None:
    @function_tool
    async def read_file(path: str) -> str:
        return _success("read_file", {"path": path, "lines": []})

    tool = enforce_tool_execution_limits(
        [read_file],
        max_tool_calls=50,
        max_identical_tool_results=99,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count}",
    )[0]

    arguments = json.dumps({"path": "src/a.py"})
    for _ in range(20):
        result = await tool.on_invoke_tool(_context(tool.name, arguments), arguments)
        assert "no_progress_rounds" not in result


# ---------------------------------------------------------------------------
# All-files-reviewed coverage nudge
# ---------------------------------------------------------------------------


async def test_coverage_nudge_emitted_when_all_files_reviewed() -> None:
    probe = _CoverageProbe(("src/a.py", "src/b.py"))

    @function_tool
    async def read_file(path: str) -> str:
        probe.mark_reviewed(path)
        return _success("read_file", {"path": path, "lines": []})

    tool = enforce_tool_execution_limits(
        [read_file],
        max_tool_calls=50,
        max_identical_tool_results=99,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count}",
        all_files_reviewed_nudge_template="All files reviewed. Submit findings or call task_done.",
        coverage_probe=probe,
    )[0]

    # Read first file — not all reviewed yet
    result = await tool.on_invoke_tool(
        _context(tool.name, json.dumps({"path": "src/a.py"})),
        json.dumps({"path": "src/a.py"}),
    )
    assert "all_files_reviewed" not in result

    # Read second file — now all reviewed
    result = await tool.on_invoke_tool(
        _context(tool.name, json.dumps({"path": "src/b.py"})),
        json.dumps({"path": "src/b.py"}),
    )
    payload = json.loads(result)
    codes = {d["code"] for d in payload["diagnostics"]}
    assert "all_files_reviewed" in codes

    # Subsequent evidence call — nudge not re-emitted
    result = await tool.on_invoke_tool(
        _context(tool.name, json.dumps({"path": "src/a.py"})),
        json.dumps({"path": "src/a.py"}),
    )
    assert "all_files_reviewed" not in result


async def test_coverage_nudge_resets_after_output_tool() -> None:
    probe = _CoverageProbe(("src/a.py",))

    @function_tool
    async def read_file(path: str) -> str:
        probe.mark_reviewed(path)
        return _success("read_file", {"path": path, "lines": []})

    @function_tool
    async def comment(content: str) -> str:
        return _success("comment", {"comment_id": "c1"})

    tools = enforce_tool_execution_limits(
        [read_file, comment],
        max_tool_calls=50,
        max_identical_tool_results=99,
        tool_timeout_seconds=1,
        tool_loop_warning_template="WARNING: {repeated_count}",
        all_files_reviewed_nudge_template="All files reviewed.",
        coverage_probe=probe,
    )
    reader = tools[0]
    commenter = tools[1]

    # Read the only file — all reviewed
    result = await reader.on_invoke_tool(
        _context(reader.name, json.dumps({"path": "src/a.py"})),
        json.dumps({"path": "src/a.py"}),
    )
    assert "all_files_reviewed" in result

    # Call comment — resets the flag
    await commenter.on_invoke_tool(
        _context(commenter.name, json.dumps({"content": "test"})),
        json.dumps({"content": "test"}),
    )

    # Read again — nudge re-emitted
    result = await reader.on_invoke_tool(
        _context(reader.name, json.dumps({"path": "src/a.py"})),
        json.dumps({"path": "src/a.py"}),
    )
    assert "all_files_reviewed" in result

"""Deterministically summarize a terminal Review execution transcript."""

import json
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from codelens.review.domain.tool_invocation import (
    ToolInvocationOutcome,
    classify_tool_result,
)
from codelens.review.domain.tool_results import ToolResultError, parse_tool_result

_STABLE_REVIEW_TOOL_NAMES = frozenset(
    {
        "comment",
        "finalize_plan",
        "finalize_verdicts",
        "find_files",
        "get_diff",
        "grep",
        "merge",
        "read_file",
        "retract_comment",
        "submit_review_plan",
        "task_done",
        "verdict",
    }
)

_HIDDEN_TRANSCRIPT_KINDS = frozenset(
    {
        "model_started",
        "model_completed",
        "model_output_completed",
        "model_reasoning_completed",
    }
)


@dataclass(frozen=True)
class ProcessTranscriptEntry:
    """Carry provider-neutral transcript fields into process report aggregation."""

    sequence: int
    kind: str
    content: str
    created_at: datetime
    metadata: dict[str, str]


@dataclass(frozen=True)
class ToolUsageSummary:
    """Aggregate calls and matched results for one stable Review tool name."""

    tool_name: str
    call_count: int
    result_count: int
    accepted_call_count: int
    rejected_call_count: int
    unclassified_call_count: int


@dataclass(frozen=True)
class RejectedToolCallSummary:
    """Expose one bounded rejected invocation diagnostic without arguments or result text."""

    agent: str
    tool_name: str
    tool_call_id: str | None
    reason_code: str
    reason: str


@dataclass(frozen=True)
class InvalidToolUsageSummary:
    """Aggregate provider-issued tool names rejected by the frozen allowlist."""

    tool_name: str
    call_count: int


@dataclass(frozen=True)
class AgentProcessSummary:
    """Aggregate provider usage and tool activity for one Agent version."""

    agent: str
    model_name: str | None
    llm_call_count: int
    checkpoint_llm_call_count: int
    input_tokens: int
    checkpoint_input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    context_compaction_count: int
    context_compacted_result_count: int
    context_compaction_original_tokens: int
    context_compaction_compressed_tokens: int
    context_compaction_failure_count: int
    compaction_replay_registered_count: int
    compaction_replay_consumed_count: int
    output_tokens: int
    checkpoint_output_tokens: int
    total_tokens: int
    tool_call_count: int
    accepted_tool_call_count: int
    rejected_tool_call_count: int
    unclassified_tool_call_count: int
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


@dataclass(frozen=True)
class ReviewProcessReport:
    """Expose bounded operational metrics for improving completed Reviews."""

    task_id: str
    status: str
    usage_is_complete: bool
    agent_run_count: int
    llm_call_count: int
    checkpoint_llm_call_count: int
    input_tokens: int
    checkpoint_input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    context_compaction_count: int
    context_compacted_result_count: int
    context_compaction_original_tokens: int
    context_compaction_compressed_tokens: int
    context_compaction_failure_count: int
    compaction_replay_registered_count: int
    compaction_replay_consumed_count: int
    output_tokens: int
    checkpoint_output_tokens: int
    total_tokens: int
    tool_call_count: int
    accepted_tool_call_count: int
    rejected_tool_call_count: int
    unclassified_tool_call_count: int
    invalid_tool_call_count: int
    tool_result_count: int
    unmatched_tool_result_count: int
    non_json_tool_result_count: int
    loop_abort_count: int
    tool_result_status_counts: dict[str, int]
    finding_count: int
    transcript_entry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    tools: tuple[ToolUsageSummary, ...]
    invalid_tools: tuple[InvalidToolUsageSummary, ...]
    rejected_tool_calls: tuple[RejectedToolCallSummary, ...]
    agents: tuple[AgentProcessSummary, ...]


@dataclass
class _AgentAccumulator:
    model_name: str | None = None
    llm_call_count: int = 0
    checkpoint_llm_call_count: int = 0
    input_tokens: int = 0
    checkpoint_input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    context_compaction_count: int = 0
    context_compacted_result_count: int = 0
    context_compaction_original_tokens: int = 0
    context_compaction_compressed_tokens: int = 0
    context_compaction_failure_count: int = 0
    compaction_replay_registered_count: int = 0
    compaction_replay_consumed_count: int = 0
    output_tokens: int = 0
    checkpoint_output_tokens: int = 0
    total_tokens: int = 0
    tool_call_count: int = 0
    accepted_tool_call_count: int = 0
    rejected_tool_call_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


def build_process_report(
    *,
    task_id: str,
    status: str,
    entries: Sequence[ProcessTranscriptEntry],
    finding_count: int,
) -> ReviewProcessReport:
    """Aggregate only trusted transcript metadata; model text never affects usage totals."""

    # Artifact order is authoritative. Older transcripts can contain duplicate sequence values.
    ordered = tuple(entries)
    agents: dict[str, _AgentAccumulator] = defaultdict(_AgentAccumulator)
    tool_calls: dict[str, int] = defaultdict(int)
    tool_results: dict[str, int] = defaultdict(int)
    accepted_tool_calls: dict[str, int] = defaultdict(int)
    rejected_tool_calls: dict[str, int] = defaultdict(int)
    invalid_tool_calls: dict[str, int] = defaultdict(int)
    calls_by_id: dict[tuple[str, str], str] = {}
    pending_calls: dict[str, deque[str]] = defaultdict(deque)
    legacy_attempt_count = 0
    legacy_usage_entries = 0
    complete_legacy_usage_entries = 0
    live_attempt_count = 0
    live_usage_entries = 0
    complete_live_usage_entries = 0
    live_usage_agents: set[str] = set()
    tool_result_count = 0
    unmatched_tool_result_count = 0
    rejected_call_summaries: list[RejectedToolCallSummary] = []
    tool_result_status_counts = {
        status: 0 for status in ("success", "partial", "needs_action", "rejected", "failed")
    }
    non_json_tool_result_count = 0
    loop_abort_count = 0

    for entry in ordered:
        agent_name = entry.metadata.get("agent", "unknown")
        accumulator = agents[agent_name]
        if entry.metadata.get("reason_code") in {
            "identical_tool_result_loop",
            "tool_loop_detected",
        }:
            loop_abort_count += 1
        if entry.kind == "model_started":
            usage_scope = entry.metadata.get("usage_scope")
            if usage_scope == "provider_call":
                live_attempt_count += 1
                live_usage_agents.add(agent_name)
            elif usage_scope != "agent_run":
                legacy_attempt_count += 1
            accumulator.started_at = _earliest(accumulator.started_at, entry.created_at)
        elif entry.kind == "model_completed":
            if entry.metadata.get("usage_scope") == "provider_call":
                live_usage_entries += 1
                live_usage_agents.add(agent_name)
                if _has_complete_usage(entry.metadata):
                    complete_live_usage_entries += 1
                    _accumulate_usage(accumulator, entry.metadata)
            accumulator.completed_at = _latest(accumulator.completed_at, entry.created_at)
        elif entry.kind == "model_output":
            # New transcripts carry one provider_call usage record per completed LLM call.
            # The terminal Agent summary remains a compatibility fallback and must not be
            # counted again when live records already exist for the same Agent.
            if agent_name not in live_usage_agents:
                legacy_usage_entries += 1
                if _has_complete_usage(entry.metadata):
                    complete_legacy_usage_entries += 1
                    _accumulate_usage(accumulator, entry.metadata)
            else:
                _accumulate_run_diagnostics(accumulator, entry.metadata)
            accumulator.completed_at = _latest(accumulator.completed_at, entry.created_at)
        elif entry.kind == "tool_call":
            tool_name = entry.metadata.get("tool_name") or _legacy_tool_name(entry.content)
            tool_name = tool_name or "unknown"
            if tool_name not in _STABLE_REVIEW_TOOL_NAMES:
                # Explicit invalid-tool events were added after some transcripts existed.
                # The frozen Review contract lets those legacy records be classified safely.
                invalid_tool_calls[tool_name] += 1
                continue
            tool_calls[tool_name] += 1
            accumulator.tool_call_count += 1
            pending_calls[agent_name].append(tool_name)
            call_id = entry.metadata.get("tool_call_id")
            if call_id:
                calls_by_id[(agent_name, call_id)] = tool_name
        elif entry.kind == "invalid_tool_call":
            tool_name = entry.metadata.get("tool_name") or _legacy_tool_name(entry.content)
            invalid_tool_calls[tool_name or "unknown"] += 1
        elif entry.kind == "tool_result":
            tool_result_count += 1
            result_status = entry.metadata.get("tool_result_status")
            if result_status not in tool_result_status_counts:
                try:
                    result_status = parse_tool_result(entry.content).status.value
                except ToolResultError:
                    result_status = None
                    non_json_tool_result_count += 1
            if result_status is not None:
                tool_result_status_counts[result_status] += 1
            call_id = entry.metadata.get("tool_call_id")
            tool_name = calls_by_id.get((agent_name, call_id)) if call_id else None
            if tool_name is not None:
                try:
                    pending_calls[agent_name].remove(tool_name)
                except ValueError:
                    pass
            elif pending_calls[agent_name]:
                tool_name = pending_calls[agent_name].popleft()
            if tool_name is None:
                unmatched_tool_result_count += 1
            else:
                tool_results[tool_name] += 1
                outcome = _tool_outcome(entry)
                if outcome.status == "rejected":
                    rejected_tool_calls[tool_name] += 1
                    accumulator.rejected_tool_call_count += 1
                    rejected_call_summaries.append(
                        RejectedToolCallSummary(
                            agent=agent_name,
                            tool_name=tool_name,
                            tool_call_id=call_id,
                            reason_code=outcome.reason_code or "tool_result_rejected",
                            reason=outcome.reason or "Tool invocation was rejected.",
                        )
                    )
                elif outcome.status == "accepted":
                    accepted_tool_calls[tool_name] += 1
                    accumulator.accepted_tool_call_count += 1

    agent_summaries = tuple(
        AgentProcessSummary(
            agent=agent_name,
            model_name=value.model_name,
            llm_call_count=value.llm_call_count,
            checkpoint_llm_call_count=value.checkpoint_llm_call_count,
            input_tokens=value.input_tokens,
            checkpoint_input_tokens=value.checkpoint_input_tokens,
            cached_input_tokens=value.cached_input_tokens,
            cache_write_input_tokens=value.cache_write_input_tokens,
            context_compaction_count=value.context_compaction_count,
            context_compacted_result_count=value.context_compacted_result_count,
            context_compaction_original_tokens=value.context_compaction_original_tokens,
            context_compaction_compressed_tokens=value.context_compaction_compressed_tokens,
            context_compaction_failure_count=value.context_compaction_failure_count,
            compaction_replay_registered_count=value.compaction_replay_registered_count,
            compaction_replay_consumed_count=value.compaction_replay_consumed_count,
            output_tokens=value.output_tokens,
            checkpoint_output_tokens=value.checkpoint_output_tokens,
            total_tokens=value.total_tokens,
            tool_call_count=value.tool_call_count,
            accepted_tool_call_count=value.accepted_tool_call_count,
            rejected_tool_call_count=value.rejected_tool_call_count,
            unclassified_tool_call_count=(
                value.tool_call_count
                - value.accepted_tool_call_count
                - value.rejected_tool_call_count
            ),
            started_at=value.started_at,
            completed_at=value.completed_at,
            duration_ms=_duration_ms(value.started_at, value.completed_at),
        )
        for agent_name, value in sorted(agents.items())
        if agent_name != "unknown" or _has_activity(value)
    )
    tools = tuple(
        ToolUsageSummary(
            tool_name,
            call_count,
            tool_results[tool_name],
            accepted_tool_calls[tool_name],
            rejected_tool_calls[tool_name],
            call_count - accepted_tool_calls[tool_name] - rejected_tool_calls[tool_name],
        )
        for tool_name, call_count in sorted(
            tool_calls.items(), key=lambda item: (-item[1], item[0])
        )
    )
    invalid_tools = tuple(
        InvalidToolUsageSummary(tool_name, call_count)
        for tool_name, call_count in sorted(
            invalid_tool_calls.items(), key=lambda item: (-item[1], item[0])
        )
    )
    started_at = ordered[0].created_at if ordered else None
    completed_at = ordered[-1].created_at if ordered else None
    agent_run_count = len(agent_summaries)
    observed_usage_entries = live_usage_entries + legacy_usage_entries
    usage_is_complete = bool(
        observed_usage_entries > 0
        and live_usage_entries == complete_live_usage_entries == live_attempt_count
        and legacy_usage_entries == complete_legacy_usage_entries == legacy_attempt_count
    )

    return ReviewProcessReport(
        task_id=task_id,
        status=status,
        usage_is_complete=usage_is_complete,
        agent_run_count=agent_run_count,
        llm_call_count=sum(agent.llm_call_count for agent in agent_summaries),
        checkpoint_llm_call_count=sum(
            agent.checkpoint_llm_call_count for agent in agent_summaries
        ),
        input_tokens=sum(agent.input_tokens for agent in agent_summaries),
        checkpoint_input_tokens=sum(
            agent.checkpoint_input_tokens for agent in agent_summaries
        ),
        cached_input_tokens=sum(agent.cached_input_tokens for agent in agent_summaries),
        cache_write_input_tokens=sum(agent.cache_write_input_tokens for agent in agent_summaries),
        context_compaction_count=sum(agent.context_compaction_count for agent in agent_summaries),
        context_compacted_result_count=sum(
            agent.context_compacted_result_count for agent in agent_summaries
        ),
        context_compaction_original_tokens=sum(
            agent.context_compaction_original_tokens for agent in agent_summaries
        ),
        context_compaction_compressed_tokens=sum(
            agent.context_compaction_compressed_tokens for agent in agent_summaries
        ),
        context_compaction_failure_count=sum(
            agent.context_compaction_failure_count for agent in agent_summaries
        ),
        compaction_replay_registered_count=sum(
            agent.compaction_replay_registered_count for agent in agent_summaries
        ),
        compaction_replay_consumed_count=sum(
            agent.compaction_replay_consumed_count for agent in agent_summaries
        ),
        output_tokens=sum(agent.output_tokens for agent in agent_summaries),
        checkpoint_output_tokens=sum(
            agent.checkpoint_output_tokens for agent in agent_summaries
        ),
        total_tokens=sum(agent.total_tokens for agent in agent_summaries),
        tool_call_count=sum(tool.call_count for tool in tools),
        accepted_tool_call_count=sum(tool.accepted_call_count for tool in tools),
        rejected_tool_call_count=sum(tool.rejected_call_count for tool in tools),
        unclassified_tool_call_count=sum(tool.unclassified_call_count for tool in tools),
        invalid_tool_call_count=sum(tool.call_count for tool in invalid_tools),
        tool_result_count=tool_result_count,
        unmatched_tool_result_count=unmatched_tool_result_count,
        non_json_tool_result_count=non_json_tool_result_count,
        loop_abort_count=loop_abort_count,
        tool_result_status_counts=tool_result_status_counts,
        finding_count=finding_count,
        transcript_entry_count=sum(
            1 for entry in ordered if entry.kind not in _HIDDEN_TRANSCRIPT_KINDS
        ),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(started_at, completed_at),
        tools=tools,
        invalid_tools=invalid_tools,
        rejected_tool_calls=tuple(rejected_call_summaries),
        agents=agent_summaries,
    )


def _tool_outcome(entry: ProcessTranscriptEntry) -> ToolInvocationOutcome:
    status = entry.metadata.get("tool_outcome")
    if status == "accepted":
        return ToolInvocationOutcome(
            "accepted",
            entry.metadata.get("tool_rejection_reason_code"),
            entry.metadata.get("tool_rejection_reason"),
        )
    if status == "rejected":
        return ToolInvocationOutcome(
            "rejected",
            entry.metadata.get("tool_rejection_reason_code"),
            entry.metadata.get("tool_rejection_reason"),
        )
    if status == "unclassified":
        return ToolInvocationOutcome("unclassified")
    return classify_tool_result(entry.content)


def _accumulate_usage(accumulator: _AgentAccumulator, metadata: dict[str, str]) -> None:
    """Add one trusted complete provider or legacy Agent usage record."""

    accumulator.llm_call_count += _non_negative_int(metadata, "llm_call_count")
    accumulator.input_tokens += _non_negative_int(metadata, "input_tokens")
    accumulator.cached_input_tokens += _non_negative_int(metadata, "cached_input_tokens")
    accumulator.cache_write_input_tokens += _non_negative_int(metadata, "cache_write_input_tokens")
    accumulator.context_compaction_count = max(
        accumulator.context_compaction_count,
        _non_negative_int(metadata, "context_compaction_count"),
    )
    accumulator.context_compacted_result_count = max(
        accumulator.context_compacted_result_count, _non_negative_int(
            metadata, "context_compacted_result_count"
        )
    )
    accumulator.context_compaction_original_tokens = max(
        accumulator.context_compaction_original_tokens, _non_negative_int(
            metadata, "context_compaction_original_tokens"
        )
    )
    accumulator.context_compaction_compressed_tokens = max(
        accumulator.context_compaction_compressed_tokens, _non_negative_int(
            metadata, "context_compaction_compressed_tokens"
        )
    )
    accumulator.context_compaction_failure_count = max(
        accumulator.context_compaction_failure_count, _non_negative_int(
            metadata, "context_compaction_failure_count"
        )
    )
    accumulator.compaction_replay_registered_count = max(
        accumulator.compaction_replay_registered_count, _non_negative_int(
            metadata, "compaction_replay_registered_count"
        )
    )
    accumulator.compaction_replay_consumed_count = max(
        accumulator.compaction_replay_consumed_count, _non_negative_int(
            metadata, "compaction_replay_consumed_count"
        )
    )
    accumulator.output_tokens += _non_negative_int(metadata, "output_tokens")
    accumulator.total_tokens += _non_negative_int(metadata, "total_tokens")
    if metadata.get("model_phase") == "checkpoint_compaction":
        accumulator.checkpoint_llm_call_count += _non_negative_int(
            metadata, "llm_call_count"
        )
        accumulator.checkpoint_input_tokens += _non_negative_int(metadata, "input_tokens")
        accumulator.checkpoint_output_tokens += _non_negative_int(metadata, "output_tokens")
    else:
        accumulator.checkpoint_llm_call_count += _non_negative_int(
            metadata, "checkpoint_llm_call_count"
        )
        accumulator.checkpoint_input_tokens += _non_negative_int(
            metadata, "checkpoint_input_tokens"
        )
        accumulator.checkpoint_output_tokens += _non_negative_int(
            metadata, "checkpoint_output_tokens"
        )
    accumulator.model_name = metadata.get("model_name") or accumulator.model_name


def _accumulate_run_diagnostics(accumulator: _AgentAccumulator, metadata: dict[str, str]) -> None:
    """Merge Agent-run diagnostics that are not present on provider response usage."""

    accumulator.context_compaction_count = max(
        accumulator.context_compaction_count,
        _non_negative_int(metadata, "context_compaction_count"),
    )
    accumulator.context_compacted_result_count = max(
        accumulator.context_compacted_result_count, _non_negative_int(
            metadata, "context_compacted_result_count"
        )
    )
    accumulator.context_compaction_original_tokens = max(
        accumulator.context_compaction_original_tokens, _non_negative_int(
            metadata, "context_compaction_original_tokens"
        )
    )
    accumulator.context_compaction_compressed_tokens = max(
        accumulator.context_compaction_compressed_tokens, _non_negative_int(
            metadata, "context_compaction_compressed_tokens"
        )
    )
    accumulator.context_compaction_failure_count = max(
        accumulator.context_compaction_failure_count, _non_negative_int(
            metadata, "context_compaction_failure_count"
        )
    )
    accumulator.compaction_replay_registered_count = max(
        accumulator.compaction_replay_registered_count, _non_negative_int(
            metadata, "compaction_replay_registered_count"
        )
    )
    accumulator.compaction_replay_consumed_count = max(
        accumulator.compaction_replay_consumed_count, _non_negative_int(
            metadata, "compaction_replay_consumed_count"
        )
    )
    accumulator.model_name = metadata.get("model_name") or accumulator.model_name


def _has_complete_usage(metadata: dict[str, str]) -> bool:
    required = ("llm_call_count", "input_tokens", "output_tokens", "total_tokens")
    return all(_is_non_negative_int(metadata.get(key)) for key in required)


def _non_negative_int(metadata: dict[str, str], key: str) -> int:
    value = metadata.get(key)
    if value is None or not _is_non_negative_int(value):
        return 0
    return int(value)


def _is_non_negative_int(value: str | None) -> bool:
    return value is not None and value.isascii() and value.isdecimal()


def _legacy_tool_name(content: str) -> str | None:
    try:
        payload: object = json.loads(content)
    except json.JSONDecodeError:
        return None

    def visit(value: object) -> str | None:
        if isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name:
                return name
            for nested in value.values():
                found = visit(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = visit(nested)
                if found is not None:
                    return found
        return None

    return visit(payload)


def _earliest(current: datetime | None, candidate: datetime) -> datetime:
    return candidate if current is None or candidate < current else current


def _latest(current: datetime | None, candidate: datetime) -> datetime:
    return candidate if current is None or candidate > current else current


def _duration_ms(started_at: datetime | None, completed_at: datetime | None) -> int | None:
    if started_at is None or completed_at is None:
        return None
    return max(0, round((completed_at - started_at).total_seconds() * 1_000))


def _has_activity(value: _AgentAccumulator) -> bool:
    return any(
        (
            value.llm_call_count,
            value.input_tokens,
            value.cached_input_tokens,
            value.cache_write_input_tokens,
            value.context_compaction_count,
            value.context_compacted_result_count,
            value.context_compaction_original_tokens,
            value.context_compaction_compressed_tokens,
            value.compaction_replay_registered_count,
            value.compaction_replay_consumed_count,
            value.output_tokens,
            value.total_tokens,
            value.tool_call_count,
            value.started_at is not None,
            value.completed_at is not None,
        )
    )

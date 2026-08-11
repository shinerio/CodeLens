"""Deterministically summarize a terminal Review execution transcript."""

import json
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

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
        "submit_review_plan",
        "task_done",
        "verdict",
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
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    context_compaction_count: int
    context_compacted_result_count: int
    context_compaction_original_bytes: int
    context_compaction_compressed_bytes: int
    output_tokens: int
    total_tokens: int
    tool_call_count: int
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
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    context_compaction_count: int
    context_compacted_result_count: int
    context_compaction_original_bytes: int
    context_compaction_compressed_bytes: int
    output_tokens: int
    total_tokens: int
    tool_call_count: int
    invalid_tool_call_count: int
    tool_result_count: int
    unmatched_tool_result_count: int
    finding_count: int
    transcript_entry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    tools: tuple[ToolUsageSummary, ...]
    invalid_tools: tuple[InvalidToolUsageSummary, ...]
    agents: tuple[AgentProcessSummary, ...]


@dataclass
class _AgentAccumulator:
    model_name: str | None = None
    llm_call_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    context_compaction_count: int = 0
    context_compacted_result_count: int = 0
    context_compaction_original_bytes: int = 0
    context_compaction_compressed_bytes: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tool_call_count: int = 0
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
    invalid_tool_calls: dict[str, int] = defaultdict(int)
    calls_by_id: dict[tuple[str, str], str] = {}
    pending_calls: dict[str, deque[str]] = defaultdict(deque)
    provider_attempt_count = 0
    usage_entries = 0
    complete_usage_entries = 0
    tool_result_count = 0
    unmatched_tool_result_count = 0

    for entry in ordered:
        agent_name = entry.metadata.get("agent", "unknown")
        accumulator = agents[agent_name]
        if entry.kind == "model_started":
            provider_attempt_count += 1
            accumulator.started_at = _earliest(accumulator.started_at, entry.created_at)
        elif entry.kind == "model_completed":
            accumulator.completed_at = _latest(accumulator.completed_at, entry.created_at)
        elif entry.kind == "model_output":
            usage_entries += 1
            if _has_complete_usage(entry.metadata):
                complete_usage_entries += 1
                accumulator.llm_call_count += _non_negative_int(entry.metadata, "llm_call_count")
                accumulator.input_tokens += _non_negative_int(entry.metadata, "input_tokens")
                accumulator.cached_input_tokens += _non_negative_int(
                    entry.metadata, "cached_input_tokens"
                )
                accumulator.cache_write_input_tokens += _non_negative_int(
                    entry.metadata, "cache_write_input_tokens"
                )
                accumulator.context_compaction_count += _non_negative_int(
                    entry.metadata, "context_compaction_count"
                )
                accumulator.context_compacted_result_count += _non_negative_int(
                    entry.metadata, "context_compacted_result_count"
                )
                accumulator.context_compaction_original_bytes += _non_negative_int(
                    entry.metadata, "context_compaction_original_bytes"
                )
                accumulator.context_compaction_compressed_bytes += _non_negative_int(
                    entry.metadata, "context_compaction_compressed_bytes"
                )
                accumulator.output_tokens += _non_negative_int(entry.metadata, "output_tokens")
                accumulator.total_tokens += _non_negative_int(entry.metadata, "total_tokens")
                accumulator.model_name = entry.metadata.get("model_name") or accumulator.model_name
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

    agent_summaries = tuple(
        AgentProcessSummary(
            agent=agent_name,
            model_name=value.model_name,
            llm_call_count=value.llm_call_count,
            input_tokens=value.input_tokens,
            cached_input_tokens=value.cached_input_tokens,
            cache_write_input_tokens=value.cache_write_input_tokens,
            context_compaction_count=value.context_compaction_count,
            context_compacted_result_count=value.context_compacted_result_count,
            context_compaction_original_bytes=value.context_compaction_original_bytes,
            context_compaction_compressed_bytes=value.context_compaction_compressed_bytes,
            output_tokens=value.output_tokens,
            total_tokens=value.total_tokens,
            tool_call_count=value.tool_call_count,
            started_at=value.started_at,
            completed_at=value.completed_at,
            duration_ms=_duration_ms(value.started_at, value.completed_at),
        )
        for agent_name, value in sorted(agents.items())
        if agent_name != "unknown" or _has_activity(value)
    )
    tools = tuple(
        ToolUsageSummary(tool_name, call_count, tool_results[tool_name])
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
    usage_is_complete = (
        usage_entries > 0
        and usage_entries == complete_usage_entries
        and usage_entries == provider_attempt_count
    )

    return ReviewProcessReport(
        task_id=task_id,
        status=status,
        usage_is_complete=usage_is_complete,
        agent_run_count=agent_run_count,
        llm_call_count=sum(agent.llm_call_count for agent in agent_summaries),
        input_tokens=sum(agent.input_tokens for agent in agent_summaries),
        cached_input_tokens=sum(agent.cached_input_tokens for agent in agent_summaries),
        cache_write_input_tokens=sum(
            agent.cache_write_input_tokens for agent in agent_summaries
        ),
        context_compaction_count=sum(
            agent.context_compaction_count for agent in agent_summaries
        ),
        context_compacted_result_count=sum(
            agent.context_compacted_result_count for agent in agent_summaries
        ),
        context_compaction_original_bytes=sum(
            agent.context_compaction_original_bytes for agent in agent_summaries
        ),
        context_compaction_compressed_bytes=sum(
            agent.context_compaction_compressed_bytes for agent in agent_summaries
        ),
        output_tokens=sum(agent.output_tokens for agent in agent_summaries),
        total_tokens=sum(agent.total_tokens for agent in agent_summaries),
        tool_call_count=sum(tool.call_count for tool in tools),
        invalid_tool_call_count=sum(tool.call_count for tool in invalid_tools),
        tool_result_count=tool_result_count,
        unmatched_tool_result_count=unmatched_tool_result_count,
        finding_count=finding_count,
        transcript_entry_count=len(ordered),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(started_at, completed_at),
        tools=tools,
        invalid_tools=invalid_tools,
        agents=agent_summaries,
    )


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
            value.context_compaction_original_bytes,
            value.context_compaction_compressed_bytes,
            value.output_tokens,
            value.total_tokens,
            value.tool_call_count,
            value.started_at is not None,
            value.completed_at is not None,
        )
    )

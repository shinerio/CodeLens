"""Epoch checkpoint compaction for cache-stable Review Agent context."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from agents import Agent
from agents.items import TResponseInputItem
from agents.run_config import CallModelData, ModelInputData
from pydantic import BaseModel, ConfigDict, Field

from codelens.review.domain.ports import AgentResponseDiagnostic
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.domain.tool_results import JsonValue
from codelens.review.infrastructure.evidence_replay import (
    EVIDENCE_TOOL_NAMES,
    ToolLoopResetSignal,
)

CHECKPOINT_SCHEMA_VERSION = "codelens_review_checkpoint_v1"
_LOGGER = logging.getLogger(__name__)
_MINIMUM_HARD_WATERMARK_GAP_BYTES = 64 * 1024
_PROVIDER_CAPABILITY_REJECTION_STATUS_CODES = frozenset({400, 404, 422})
_EVIDENCE_ID_PATTERN = re.compile(r"\bevidence_[a-f0-9]{24}\b")


class ContextCheckpointError(RuntimeError):
    """Raised when context cannot safely advance beyond the hard watermark."""


class CheckpointEvidenceConclusion(BaseModel):
    """Bind one compacted conclusion exclusively to host-issued evidence IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence_ids: list[str] = Field(min_length=1)
    conclusion: str = Field(min_length=1)


class CheckpointEliminatedHypothesis(BaseModel):
    """Retain why a hypothesis was eliminated and the evidence that supports it."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence_ids: list[str] = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CheckpointSummary(BaseModel):
    """Strict semantic state returned by the independent checkpoint model call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["codelens_review_checkpoint_v1"]
    investigation_summary: str = Field(min_length=1)
    evidence_conclusions: list[CheckpointEvidenceConclusion]
    eliminated_hypotheses: list[CheckpointEliminatedHypothesis]
    open_investigations: list[str]
    next_actions: list[str]


class CheckpointEvidenceReference(BaseModel):
    """Host-derived evidence identity and canonical re-read arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    original_bytes: int = Field(ge=0)


@dataclass(frozen=True)
class CheckpointSummaryRequest:
    """Supply an untrusted complete history segment to the checkpoint call."""

    prompt: str
    previous_summary: CheckpointSummary | None
    compacted_items: tuple[dict[str, object], ...]
    evidence_index: tuple[CheckpointEvidenceReference, ...]

    def model_input(self) -> str:
        """Serialize source history with explicit trust boundaries and stable ordering."""

        return _canonical_json(
            {
                "previous_checkpoint": (
                    None
                    if self.previous_summary is None
                    else self.previous_summary.model_dump(mode="json")
                ),
                "host_evidence_index": [
                    item.model_dump(mode="json") for item in self.evidence_index
                ],
                "untrusted_transcript_segment": list(self.compacted_items),
            }
        )


@dataclass(frozen=True)
class CheckpointSummaryResult:
    """Return validated semantic state and bounded usage diagnostics."""

    summary: CheckpointSummary
    diagnostics: tuple[AgentResponseDiagnostic, ...]


class CheckpointSummarizerPort(Protocol):
    """Create one semantic checkpoint without inheriting the main Agent history."""

    async def summarize(
        self,
        request: CheckpointSummaryRequest,
        agent: Agent[Any],
    ) -> CheckpointSummaryResult:
        """Summarize complete old rounds through an independent model call."""

        raise NotImplementedError


@dataclass
class ContextCheckpointTracker:
    """Hold the single current checkpoint and per-run compaction measurements."""

    checkpoint_count: int = 0
    compacted_result_count: int = 0
    original_bytes: int = 0
    compressed_bytes: int = 0
    covered_item_count: int = 0
    immutable_prefix_count: int = 0
    checkpoint_item: TResponseInputItem | None = None
    previous_summary: CheckpointSummary | None = None
    evidence_index: tuple[CheckpointEvidenceReference, ...] = ()
    diagnostics: list[AgentResponseDiagnostic] = field(default_factory=list)
    checkpoint_payloads: list[str] = field(default_factory=list)
    failure_count: int = 0
    consecutive_failure_count: int = 0
    last_failure_item_count: int | None = None
    is_disabled: bool = False
    disabled_reason: str | None = None

    def effective_input(
        self, raw_items: list[TResponseInputItem]
    ) -> list[TResponseInputItem]:
        """Project append-only raw history into prefix + checkpoint + active tail."""

        if self.checkpoint_item is None:
            return raw_items
        return [
            *raw_items[: self.immutable_prefix_count],
            self.checkpoint_item,
            *raw_items[self.covered_item_count :],
        ]

    def reset_context(self) -> None:
        """Drop failed-attempt context state while retaining spent-call diagnostics."""

        self.covered_item_count = 0
        self.immutable_prefix_count = 0
        self.checkpoint_item = None
        self.previous_summary = None
        self.evidence_index = ()
        self.last_failure_item_count = None
        self.checkpoint_count = 0
        self.compacted_result_count = 0
        self.original_bytes = 0
        self.compressed_bytes = 0
        self.checkpoint_payloads.clear()


@dataclass(frozen=True)
class _EvidenceOutput:
    reference: CheckpointEvidenceReference
    encoded_size: int


@dataclass(frozen=True)
class _CompleteRound:
    start: int
    end: int
    evidence: tuple[_EvidenceOutput, ...]


def build_context_checkpoint_filter(
    *,
    limits: ToolLimits,
    prompt: str,
    tracker: ContextCheckpointTracker,
    summarizer: CheckpointSummarizerPort,
    loop_reset_signal: ToolLoopResetSignal | None = None,
) -> Callable[[CallModelData[object]], Awaitable[ModelInputData]]:
    """Build an async pre-call filter that advances only on complete old rounds."""

    async def checkpoint(data: CallModelData[object]) -> ModelInputData:
        raw_items = [cast(TResponseInputItem, dict(item)) for item in data.model_data.input]
        if not limits.context_compaction_enabled or not raw_items:
            return ModelInputData(input=raw_items, instructions=data.model_data.instructions)

        if tracker.immutable_prefix_count == 0:
            tracker.immutable_prefix_count = _immutable_prefix_count(raw_items)
            tracker.covered_item_count = tracker.immutable_prefix_count

        rounds = _complete_rounds(raw_items, tracker.covered_item_count)
        evidence_outputs = [evidence for round_ in rounds for evidence in round_.evidence]
        active_bytes = sum(
            len(_json_text(item).encode("utf-8"))
            for item in raw_items[tracker.covered_item_count :]
        )
        hard_watermark = _hard_watermark(limits)
        if tracker.is_disabled:
            if active_bytes >= hard_watermark:
                raise ContextCheckpointError(
                    "checkpoint compaction is disabled beyond the hard context watermark"
                )
            return ModelInputData(
                input=tracker.effective_input(raw_items),
                instructions=data.model_data.instructions,
            )
        if active_bytes < limits.context_compaction_trigger_bytes:
            return ModelInputData(
                input=tracker.effective_input(raw_items),
                instructions=data.model_data.instructions,
            )
        if tracker.last_failure_item_count == len(raw_items):
            return ModelInputData(
                input=tracker.effective_input(raw_items),
                instructions=data.model_data.instructions,
            )

        compactable_result_count = max(
            0,
            len(evidence_outputs) - limits.context_compaction_keep_recent_evidence_results,
        )
        selected_result_count = 0
        selected_bytes = 0
        covered_end = tracker.covered_item_count
        for round_ in rounds:
            round_result_count = len(round_.evidence)
            if round_result_count == 0:
                continue
            if selected_result_count + round_result_count > compactable_result_count:
                break
            selected_result_count += round_result_count
            selected_bytes += sum(item.encoded_size for item in round_.evidence)
            covered_end = round_.end

        if selected_result_count == 0 or covered_end <= tracker.covered_item_count:
            return ModelInputData(
                input=tracker.effective_input(raw_items),
                instructions=data.model_data.instructions,
            )

        newly_compacted = tuple(
            cast(dict[str, object], dict(item))
            for item in raw_items[tracker.covered_item_count : covered_end]
        )
        new_references = tuple(
            evidence.reference
            for round_ in rounds
            if round_.end <= covered_end
            for evidence in round_.evidence
        )
        evidence_index = (*tracker.evidence_index, *new_references)
        request = CheckpointSummaryRequest(
            prompt=prompt,
            previous_summary=tracker.previous_summary,
            compacted_items=newly_compacted,
            evidence_index=evidence_index,
        )
        max_retries = limits.context_compaction_max_retries
        retry_backoff_base = limits.context_compaction_retry_backoff_base
        retry_max_delay = limits.context_compaction_retry_max_delay
        result: CheckpointSummaryResult | None = None
        last_error: Exception | None = None
        for retry_attempt in range(max_retries + 1):
            try:
                result = await summarizer.summarize(request, data.agent)
                _validate_evidence_references(result.summary, evidence_index)
                break
            except Exception as error:
                last_error = error
                result = None
                if retry_attempt < max_retries:
                    delay = min(
                        retry_backoff_base * (2**retry_attempt),
                        retry_max_delay,
                    )
                    _LOGGER.warning(
                        "Checkpoint compaction attempt %d failed, retrying in %.1fs",
                        retry_attempt + 1,
                        delay,
                        extra={"error_type": type(error).__name__},
                    )
                    await asyncio.sleep(delay)
                    continue
        if result is None:
            assert last_error is not None
            tracker.failure_count += 1
            tracker.consecutive_failure_count += 1
            tracker.last_failure_item_count = len(raw_items)
            if _is_provider_capability_rejection(last_error):
                tracker.is_disabled = True
                tracker.disabled_reason = "provider_compaction_unsupported"
            elif (
                tracker.consecutive_failure_count
                >= limits.context_compaction_max_consecutive_failures
            ):
                tracker.is_disabled = True
                tracker.disabled_reason = "checkpoint_failure_circuit_open"
            if active_bytes >= hard_watermark:
                raise ContextCheckpointError(
                    "checkpoint compaction failed beyond the hard context watermark"
                ) from last_error
            _LOGGER.warning(
                "Checkpoint compaction failed below the hard watermark after %d attempts",
                max_retries + 1,
                extra={
                    "error_type": type(last_error).__name__,
                    "circuit_open": tracker.is_disabled,
                    "disabled_reason": tracker.disabled_reason,
                },
            )
            return ModelInputData(
                input=tracker.effective_input(raw_items),
                instructions=data.model_data.instructions,
            )
        checkpoint_item = _checkpoint_item(result.summary, evidence_index)

        tracker.checkpoint_count += 1
        tracker.compacted_result_count += selected_result_count
        tracker.original_bytes += selected_bytes
        tracker.compressed_bytes += len(_canonical_json(checkpoint_item).encode("utf-8"))
        tracker.covered_item_count = covered_end
        tracker.checkpoint_item = checkpoint_item
        tracker.previous_summary = result.summary
        tracker.evidence_index = evidence_index
        tracker.diagnostics.extend(result.diagnostics)
        tracker.checkpoint_payloads.append(_checkpoint_content(result.summary, evidence_index))
        tracker.consecutive_failure_count = 0
        tracker.last_failure_item_count = None
        if loop_reset_signal is not None:
            loop_reset_signal.trigger()
        return ModelInputData(
            input=tracker.effective_input(raw_items),
            instructions=data.model_data.instructions,
        )

    return checkpoint


def checkpoint_summary_from_text(payload: str) -> CheckpointSummary:
    """Wrap model-authored prose in the host-owned checkpoint schema.

    Markdown fences are stripped leniently: a truncated closing fence (caused by
    max_tokens cutoff) no longer fails validation — the opening fence line is
    removed and the remaining text is accepted as the summary body.
    """

    value = payload.strip()
    if value.startswith("```"):
        first_newline = value.find("\n")
        if first_newline < 0:
            value = ""
        elif value.endswith("```"):
            value = value[first_newline + 1 : -3].strip()
        else:
            value = value[first_newline + 1 :].strip()
    return CheckpointSummary(
        schema_version="codelens_review_checkpoint_v1",
        investigation_summary=value,
        evidence_conclusions=[],
        eliminated_hypotheses=[],
        open_investigations=[],
        next_actions=[],
    )


def _hard_watermark(limits: ToolLimits) -> int:
    return max(
        limits.context_compaction_trigger_bytes * 2,
        limits.context_compaction_trigger_bytes + _MINIMUM_HARD_WATERMARK_GAP_BYTES,
    )


def _is_provider_capability_rejection(error: BaseException) -> bool:
    """Detect bounded provider rejections that should not be retried every turn."""

    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status_code = getattr(current, "status_code", None)
        if status_code in _PROVIDER_CAPABILITY_REJECTION_STATUS_CODES:
            return True
        current = current.__cause__ or current.__context__
    return False


def _immutable_prefix_count(items: list[TResponseInputItem]) -> int:
    count = 0
    for item in items:
        if item.get("type") != "message" or item.get("role") != "user":
            break
        count += 1
    return count or 1


def _complete_rounds(
    items: list[TResponseInputItem], start: int
) -> tuple[_CompleteRound, ...]:
    calls_by_id: dict[str, tuple[str, object]] = {}
    for item in items[start:]:
        if item.get("type") != "function_call":
            continue
        call_id = item.get("call_id")
        tool_name = item.get("name")
        if isinstance(call_id, str) and isinstance(tool_name, str):
            calls_by_id[call_id] = (tool_name, item.get("arguments", "{}"))

    rounds: list[_CompleteRound] = []
    round_start = start
    cursor = start
    while cursor < len(items):
        if items[cursor].get("type") != "function_call_output":
            cursor += 1
            continue
        output_end = cursor
        evidence: list[_EvidenceOutput] = []
        while (
            output_end < len(items)
            and items[output_end].get("type") == "function_call_output"
        ):
            output_item = items[output_end]
            call_id = output_item.get("call_id")
            if isinstance(call_id, str) and call_id in calls_by_id:
                tool_name, arguments = calls_by_id[call_id]
                if tool_name in EVIDENCE_TOOL_NAMES:
                    output = output_item.get("output", "")
                    encoded_size = len(_json_text(output).encode("utf-8"))
                    reference = _evidence_reference(
                        call_id,
                        tool_name,
                        arguments,
                        output,
                        encoded_size,
                    )
                    if reference is not None:
                        evidence.append(_EvidenceOutput(reference, encoded_size))
            output_end += 1
        rounds.append(_CompleteRound(round_start, output_end, tuple(evidence)))
        round_start = output_end
        cursor = output_end
    return tuple(rounds)


def _evidence_reference(
    call_id: str,
    tool_name: str,
    arguments: object,
    output: object,
    encoded_size: int,
) -> CheckpointEvidenceReference | None:
    if not isinstance(arguments, str):
        return None
    try:
        parsed_arguments: object = json.loads(arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed_arguments, dict) or not all(
        isinstance(key, str) for key in parsed_arguments
    ):
        return None
    canonical_arguments = cast(dict[str, JsonValue], parsed_arguments)
    identity = _canonical_json(
        {
            "call_id": call_id,
            "tool_name": tool_name,
            "arguments": canonical_arguments,
            "output": output,
        }
    )
    evidence_id = f"evidence_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    return CheckpointEvidenceReference(
        evidence_id=evidence_id,
        tool_name=tool_name,
        arguments=canonical_arguments,
        original_bytes=encoded_size,
    )


def _validate_evidence_references(
    summary: CheckpointSummary,
    evidence_index: tuple[CheckpointEvidenceReference, ...],
) -> None:
    known_ids = {item.evidence_id for item in evidence_index}
    prose_ids = set(_EVIDENCE_ID_PATTERN.findall(summary.investigation_summary))
    referenced_groups = (
        tuple(prose_ids),
        *(conclusion.evidence_ids for conclusion in summary.evidence_conclusions),
        *(hypothesis.evidence_ids for hypothesis in summary.eliminated_hypotheses),
    )
    for evidence_ids in referenced_groups:
        unknown_ids = set(evidence_ids) - known_ids
        if unknown_ids:
            unknown = ", ".join(sorted(unknown_ids))
            raise ValueError(f"checkpoint contains unknown evidence ID: {unknown}")


def _checkpoint_item(
    summary: CheckpointSummary,
    evidence_index: tuple[CheckpointEvidenceReference, ...],
) -> TResponseInputItem:
    return cast(
        TResponseInputItem,
        {
            "type": "message",
            "role": "user",
            "content": _checkpoint_content(summary, evidence_index),
        },
    )


def _checkpoint_content(
    summary: CheckpointSummary,
    evidence_index: tuple[CheckpointEvidenceReference, ...],
) -> str:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "host_state": {
            "evidence_index": [item.model_dump(mode="json") for item in evidence_index]
        },
        "semantic_summary": summary.model_dump(mode="json"),
    }
    return f"<review-checkpoint>\n{_canonical_json(payload)}\n</review-checkpoint>"


def _json_text(value: object) -> str:
    return value if isinstance(value, str) else _canonical_json(value)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

import json
from typing import Any

from agents import Agent
from agents.run_config import CallModelData, ModelInputData

from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.context_checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEvidenceConclusion,
    CheckpointEvidenceReference,
    CheckpointSummary,
    CheckpointSummaryRequest,
    CheckpointSummaryResult,
    ContextCheckpointError,
    ContextCheckpointTracker,
    build_context_checkpoint_filter,
)


class RecordingSummarizer:
    def __init__(self) -> None:
        self.requests: list[CheckpointSummaryRequest] = []

    async def summarize(self, request: Any, agent: Agent[Any]) -> CheckpointSummaryResult:
        del agent
        self.requests.append(request)
        evidence_ids = [item.evidence_id for item in request.evidence_index]
        return CheckpointSummaryResult(
            summary=CheckpointSummary(
                schema_version=CHECKPOINT_SCHEMA_VERSION,
                investigation_summary=f"checkpoint-{len(self.requests)}",
                evidence_conclusions=[
                    CheckpointEvidenceConclusion(
                        evidence_ids=evidence_ids,
                        conclusion="The covered evidence was inspected.",
                    )
                ],
                eliminated_hypotheses=[],
                open_investigations=[],
                next_actions=["Continue with the active tail."],
            ),
            diagnostics=(),
        )


def _round(index: int, *, output_size: int = 4000) -> list[dict[str, object]]:
    call_id = f"call-{index}"
    return [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": f"inspect {index}"}],
        },
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "read_file",
            "arguments": json.dumps({"path": f"src/{index}.py"}),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": str(index) * output_size,
        },
    ]


def _parallel_round(index: int, *, output_size: int = 4000) -> list[dict[str, object]]:
    return [
        {
            "type": "function_call",
            "call_id": f"call-{index}-a",
            "name": "read_file",
            "arguments": json.dumps({"path": f"src/{index}-a.py"}),
        },
        {
            "type": "function_call",
            "call_id": f"call-{index}-b",
            "name": "get_diff",
            "arguments": json.dumps({"path": f"src/{index}-b.py"}),
        },
        {
            "type": "function_call_output",
            "call_id": f"call-{index}-a",
            "output": "a" * output_size,
        },
        {
            "type": "function_call_output",
            "call_id": f"call-{index}-b",
            "output": "b" * output_size,
        },
    ]


def _model_data(items: list[dict[str, object]]) -> CallModelData[object]:
    return CallModelData(
        ModelInputData(input=items, instructions="stable-system-prompt"),
        Agent(name="test-agent", instructions="test"),
        None,
    )


async def test_checkpoint_preserves_initial_input_and_complete_parallel_rounds() -> None:
    initial = {"type": "message", "role": "user", "content": "immutable input"}
    items = [initial, *_parallel_round(0), *_round(1, output_size=100)]
    summarizer = RecordingSummarizer()
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=6000,
            context_compaction_keep_recent_evidence_results=1,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=summarizer,
    )

    filtered = await input_filter(_model_data(items))

    assert filtered.instructions == "stable-system-prompt"
    assert filtered.input[0] == initial
    assert filtered.input[1]["role"] == "user"
    assert "<review-checkpoint>" in str(filtered.input[1]["content"])
    assert filtered.input[2:] == items[5:]
    assert len(summarizer.requests) == 1
    request = summarizer.requests[0]
    assert len(request.compacted_items) == 4
    assert {item.tool_name for item in request.evidence_index} == {"read_file", "get_diff"}
    assert tracker.checkpoint_count == 1
    assert tracker.compacted_result_count == 2


async def test_checkpoint_bytes_stay_identical_until_next_epoch() -> None:
    initial = {"type": "message", "role": "user", "content": "immutable input"}
    first_history = [initial, *_round(0), *_round(1, output_size=100)]
    summarizer = RecordingSummarizer()
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=1,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=summarizer,
    )

    first = await input_filter(_model_data(first_history))
    checkpoint = first.input[1]
    extended_history = [*first_history, *_round(2, output_size=100)]
    second = await input_filter(_model_data(extended_history))

    assert second.input[0] == initial
    assert second.input[1] == checkpoint
    assert second.input[2:] == extended_history[4:]
    assert len(summarizer.requests) == 1
    assert tracker.checkpoint_count == 1


async def test_next_epoch_replaces_checkpoint_and_carries_previous_summary() -> None:
    initial = {"type": "message", "role": "user", "content": "immutable input"}
    history = [initial, *_round(0), *_round(1, output_size=100)]
    summarizer = RecordingSummarizer()
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=1,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=summarizer,
    )

    first = await input_filter(_model_data(history))
    extended_history = [*history, *_round(2), *_round(3, output_size=100)]
    second = await input_filter(_model_data(extended_history))

    assert len(summarizer.requests) == 2
    assert summarizer.requests[0].previous_summary is None
    assert summarizer.requests[1].previous_summary is not None
    assert summarizer.requests[1].previous_summary.investigation_summary == "checkpoint-1"
    assert second.input[0] == initial
    assert second.input[1] != first.input[1]
    assert sum(item.get("role") == "user" for item in second.input) == 2
    assert tracker.checkpoint_count == 2
    assert tracker.compacted_result_count == 3


async def test_checkpoint_prose_does_not_treat_generic_evidence_words_as_ids() -> None:
    class ProseSummarizer(RecordingSummarizer):
        async def summarize(
            self, request: Any, agent: Agent[Any]
        ) -> CheckpointSummaryResult:
            result = await super().summarize(request, agent)
            prose = result.summary.model_copy(
                update={
                    "investigation_summary": (
                        "The investigation remains evidence_based; evidence_unknown is prose, "
                        f"while {request.evidence_index[0].evidence_id} is a host-issued ID."
                    )
                }
            )
            return CheckpointSummaryResult(summary=prose, diagnostics=())

    initial = {"type": "message", "role": "user", "content": "immutable input"}
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=0,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=ProseSummarizer(),
    )

    filtered = await input_filter(_model_data([initial, *_round(0)]))

    assert tracker.checkpoint_count == 1
    assert "<review-checkpoint>" in str(filtered.input[1]["content"])


async def test_invalid_checkpoint_keeps_existing_context_and_is_not_retried_unchanged() -> None:
    class InvalidSummarizer(RecordingSummarizer):
        async def summarize(
            self, request: Any, agent: Agent[Any]
        ) -> CheckpointSummaryResult:
            result = await super().summarize(request, agent)
            invalid = result.summary.model_copy(
                update={
                    "evidence_conclusions": [
                        CheckpointEvidenceConclusion(
                            evidence_ids=["evidence_" + "0" * 24],
                            conclusion="Unsupported claim.",
                        )
                    ]
                }
            )
            return CheckpointSummaryResult(summary=invalid, diagnostics=())

    initial = {"type": "message", "role": "user", "content": "immutable input"}
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=0,
            context_compaction_max_retries=0,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=InvalidSummarizer(),
    )

    history = [initial, *_round(0)]
    first = await input_filter(_model_data(history))
    second = await input_filter(_model_data(history))

    assert first.input == history
    assert second.input == history
    assert tracker.failure_count == 1
    assert tracker.checkpoint_count == 0


async def test_invalid_checkpoint_fails_explicitly_beyond_hard_watermark() -> None:
    class FailingSummarizer(RecordingSummarizer):
        async def summarize(
            self, request: Any, agent: Agent[Any]
        ) -> CheckpointSummaryResult:
            del request, agent
            raise ValueError("invalid structured checkpoint")

    initial = {"type": "message", "role": "user", "content": "immutable input"}
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=0,
            context_compaction_max_retries=0,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=FailingSummarizer(),
    )

    try:
        await input_filter(_model_data([initial, *_round(0, output_size=70_000)]))
    except ContextCheckpointError as error:
        assert "hard context watermark" in str(error)
    else:
        raise AssertionError("hard-watermark checkpoint failure must stop the run")


async def test_provider_capability_rejection_disables_checkpoint_for_the_run() -> None:
    class ProviderRejectedError(RuntimeError):
        status_code = 400

    class RejectedSummarizer(RecordingSummarizer):
        async def summarize(
            self, request: Any, agent: Agent[Any]
        ) -> CheckpointSummaryResult:
            del agent
            self.requests.append(request)
            raise ProviderRejectedError("structured output is unsupported")

    initial = {"type": "message", "role": "user", "content": "immutable input"}
    summarizer = RejectedSummarizer()
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=0,
            context_compaction_max_retries=0,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=summarizer,
    )

    history = [initial, *_round(0)]
    first = await input_filter(_model_data(history))
    extended = [*history, *_round(1)]
    second = await input_filter(_model_data(extended))

    assert first.input == history
    assert second.input == extended
    assert len(summarizer.requests) == 1
    assert tracker.failure_count == 1
    assert tracker.is_disabled is True
    assert tracker.disabled_reason == "provider_compaction_unsupported"


async def test_repeated_checkpoint_failures_open_circuit_after_three_attempts() -> None:
    class FailingSummarizer(RecordingSummarizer):
        async def summarize(
            self, request: Any, agent: Agent[Any]
        ) -> CheckpointSummaryResult:
            del agent
            self.requests.append(request)
            raise ValueError("invalid checkpoint JSON")

    initial = {"type": "message", "role": "user", "content": "immutable input"}
    summarizer = FailingSummarizer()
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=0,
            context_compaction_max_retries=0,
            context_compaction_max_consecutive_failures=3,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=summarizer,
    )

    history = [initial]
    for index in range(4):
        history = [*history, *_round(index)]
        filtered = await input_filter(_model_data(history))
        assert filtered.input == history

    assert len(summarizer.requests) == 3
    assert tracker.failure_count == 3
    assert tracker.is_disabled is True
    assert tracker.disabled_reason == "checkpoint_failure_circuit_open"


def test_reset_context_drops_metrics_from_an_abandoned_attempt() -> None:
    tracker = ContextCheckpointTracker(
        checkpoint_count=2,
        compacted_result_count=7,
        original_bytes=123_456,
        compressed_bytes=2_048,
        covered_item_count=9,
        immutable_prefix_count=1,
        checkpoint_item={"type": "message", "role": "user", "content": "old"},
        evidence_index=(CheckpointEvidenceReference(
            evidence_id="evidence_" + "a" * 24,
            tool_name="read_file",
            arguments={"path": "src/example.py"},
            original_bytes=100,
        ),),
        failure_count=2,
        consecutive_failure_count=1,
        last_failure_item_count=10,
    )
    tracker.checkpoint_payloads.extend(("old checkpoint",))

    tracker.reset_context()

    assert tracker.checkpoint_count == 0
    assert tracker.compacted_result_count == 0
    assert tracker.original_bytes == 0
    assert tracker.compressed_bytes == 0
    assert tracker.checkpoint_payloads == []
    assert tracker.covered_item_count == 0
    assert tracker.immutable_prefix_count == 0
    assert tracker.checkpoint_item is None
    assert tracker.evidence_index == ()
    assert tracker.last_failure_item_count is None


async def test_checkpoint_retry_recovers_after_transient_empty_output() -> None:
    """A transient summarizer failure (e.g. empty output) must be retried.

    The first summarize() call raises ValueError (simulating the empty-output
    detection from _SdkCheckpointSummarizer); the second call succeeds. The
    checkpoint must succeed on the retry, and consecutive_failure_count must
    stay at 0 because the overall attempt succeeded.
    """

    class TransientEmptySummarizer(RecordingSummarizer):
        def __init__(self) -> None:
            super().__init__()
            self.attempt = 0

        async def summarize(
            self, request: Any, agent: Agent[Any]
        ) -> CheckpointSummaryResult:
            self.attempt += 1
            if self.attempt == 1:
                raise ValueError("checkpoint model returned empty output")
            return await super().summarize(request, agent)

    initial = {"type": "message", "role": "user", "content": "immutable input"}
    summarizer = TransientEmptySummarizer()
    tracker = ContextCheckpointTracker()
    input_filter = build_context_checkpoint_filter(
        limits=ToolLimits(
            context_compaction_trigger_bytes=3000,
            context_compaction_keep_recent_evidence_results=0,
            context_compaction_max_retries=2,
            context_compaction_retry_backoff_base=0.1,
            context_compaction_retry_max_delay=1.0,
        ),
        prompt="Create a checkpoint.",
        tracker=tracker,
        summarizer=summarizer,
    )

    history = [initial, *_round(0)]
    filtered = await input_filter(_model_data(history))

    assert "<review-checkpoint>" in str(filtered.input[1]["content"])
    assert len(summarizer.requests) == 1
    assert tracker.checkpoint_count == 1
    assert tracker.failure_count == 0
    assert tracker.consecutive_failure_count == 0
    assert tracker.is_disabled is False

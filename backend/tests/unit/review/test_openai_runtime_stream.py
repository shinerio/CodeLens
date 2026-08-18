import json
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace

from agents import RawResponsesStreamEvent, RunItemStreamEvent

from codelens.review.infrastructure.openai_runtime import _visible_event


@dataclass
class _LockBearingToolCallItem:
    raw_item: object
    agent_lock: object = field(default_factory=threading.RLock)


@dataclass
class _LockBearingToolResultItem:
    raw_item: object
    output: object
    agent_lock: object = field(default_factory=threading.RLock)


def test_stream_events_skip_token_deltas_and_done_events() -> None:
    """Token deltas and *.done events are handled in the stream loop, not here."""

    output_delta_events = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.output_text.delta",
                delta="# Result",
                item_id="output-1",
                content_index=0,
            )
        )
    )
    output_done_events = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.output_text.done",
                text="# Result",
                item_id="output-1",
                content_index=0,
            )
        )
    )
    reasoning_delta_events = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.reasoning_summary_text.delta",
                delta="## Plan",
                item_id="reasoning-1",
                summary_index=0,
            )
        )
    )
    reasoning_done_events = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.reasoning_summary_text.done",
                summary="## Plan",
                item_id="reasoning-1",
                summary_index=0,
            )
        )
    )

    assert output_delta_events == []
    assert output_done_events == []
    assert reasoning_delta_events == []
    assert reasoning_done_events == []


def test_stream_events_ignore_incremental_tool_arguments() -> None:
    event = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.function_call_arguments.delta",
                delta='{"path":"example.py"}',
            )
        )
    )

    assert event == []


def test_stream_response_boundaries_expose_live_provider_usage() -> None:
    started = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp-1"))
        )
    )
    completed = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp-1",
                    model="gpt-5.1",
                    usage=SimpleNamespace(
                        input_tokens=120,
                        output_tokens=30,
                        total_tokens=150,
                        input_tokens_details=SimpleNamespace(
                            cached_tokens=80,
                        ),
                    ),
                ),
            )
        )
    )

    assert len(started) == 1
    assert len(completed) == 1
    assert started[0].kind == "model_started"
    assert started[0].metadata == {
        "response_id": "resp-1",
        "usage_scope": "provider_call",
        "event_role": "marker",
    }
    assert completed[0].kind == "model_completed"
    assert completed[0].metadata == {
        "response_id": "resp-1",
        "usage_scope": "provider_call",
        "model_name": "gpt-5.1",
        "event_role": "marker",
        "llm_call_count": "1",
        "input_tokens": "120",
        "cached_input_tokens": "80",
        "output_tokens": "30",
        "total_tokens": "150",
    }


def test_stream_tool_events_include_stable_name_and_call_identity() -> None:
    raw_call = SimpleNamespace(name="read_file", call_id="call-1")
    tool_call_events = _visible_event(
        RunItemStreamEvent(
            name="tool_called",
            item=SimpleNamespace(raw_item=raw_call),
        )
    )
    result = (
        '{"schema_version":"2","tool":"read_file","status":"success","data":{},"diagnostics":[]}'
    )
    tool_result_events = _visible_event(
        RunItemStreamEvent(
            name="tool_output",
            item=SimpleNamespace(
                raw_item=SimpleNamespace(call_id="call-1"),
                output=result,
            ),
        )
    )

    assert len(tool_call_events) == 1
    assert len(tool_result_events) == 1
    tool_call = tool_call_events[0]
    tool_result = tool_result_events[0]
    assert tool_call.metadata == {"tool_call_id": "call-1", "tool_name": "read_file"}
    assert tool_result.content == result
    assert tool_result.metadata == {
        "tool_call_id": "call-1",
        "tool_outcome": "accepted",
        "tool_result_status": "success",
    }


def test_stream_tool_result_records_bounded_rejection_reason() -> None:
    tool_result_events = _visible_event(
        RunItemStreamEvent(
            name="tool_output",
            item=_LockBearingToolResultItem(
                raw_item=SimpleNamespace(call_id="call-rejected"),
                output=(
                    "An error occurred while running the tool. Please try again. Error: "
                    "Invalid JSON input for tool comment: extra inputs are not permitted"
                ),
            ),
        )
    )

    assert len(tool_result_events) == 1
    assert tool_result_events[0].metadata == {
        "tool_call_id": "call-rejected",
        "tool_outcome": "unclassified",
        "non_json_tool_result": "true",
    }


def test_stream_tool_events_exclude_non_serializable_sdk_runtime_state() -> None:
    raw_call = {"name": "read_file", "call_id": "call-1", "arguments": '{"path":"a.py"}'}
    tool_call_events = _visible_event(
        RunItemStreamEvent(
            name="tool_called",
            item=_LockBearingToolCallItem(raw_item=raw_call),
        )
    )
    tool_result_events = _visible_event(
        RunItemStreamEvent(
            name="tool_output",
            item=_LockBearingToolResultItem(
                raw_item=SimpleNamespace(call_id="call-1"),
                output={"path": "a.py", "content": "bounded"},
            ),
        )
    )

    assert len(tool_call_events) == 1
    assert len(tool_result_events) == 1
    tool_call = tool_call_events[0]
    tool_result = tool_result_events[0]
    assert json.loads(tool_call.content) == raw_call
    assert json.loads(tool_result.content) == {"content": "bounded", "path": "a.py"}
    assert tool_result.metadata["tool_outcome"] == "unclassified"
    assert tool_result.metadata["non_json_tool_result"] == "true"

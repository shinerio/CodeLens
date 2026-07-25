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


def test_stream_events_include_message_boundaries_for_markdown_rendering() -> None:
    output_delta = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.output_text.delta",
                delta="# Result",
                item_id="output-1",
                content_index=0,
            )
        )
    )
    output_completed = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.output_text.done",
                item_id="output-1",
                content_index=0,
            )
        )
    )
    reasoning_delta = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.reasoning_summary_text.delta",
                delta="## Plan",
                item_id="reasoning-1",
                summary_index=0,
            )
        )
    )
    reasoning_completed = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.reasoning_summary_text.done",
                item_id="reasoning-1",
                summary_index=0,
            )
        )
    )

    assert output_delta is not None
    assert output_completed is not None
    assert reasoning_delta is not None
    assert reasoning_completed is not None
    assert (output_delta.kind, output_delta.metadata) == (
        "model_output_delta",
        {"message_id": "output-1:0"},
    )
    assert (output_completed.kind, output_completed.metadata) == (
        "model_output_completed",
        {"message_id": "output-1:0"},
    )
    assert (reasoning_delta.kind, reasoning_delta.metadata) == (
        "model_reasoning_delta",
        {"message_id": "reasoning-1:0"},
    )
    assert (reasoning_completed.kind, reasoning_completed.metadata) == (
        "model_reasoning_completed",
        {"message_id": "reasoning-1:0"},
    )


def test_stream_events_ignore_incremental_tool_arguments() -> None:
    event = _visible_event(
        RawResponsesStreamEvent(
            data=SimpleNamespace(
                type="response.function_call_arguments.delta",
                delta='{"path":"example.py"}',
            )
        )
    )

    assert event is None


def test_stream_tool_events_include_stable_name_and_call_identity() -> None:
    raw_call = SimpleNamespace(name="read_file", call_id="call-1")
    tool_call = _visible_event(
        RunItemStreamEvent(
            name="tool_called",
            item=SimpleNamespace(raw_item=raw_call),
        )
    )
    tool_result = _visible_event(
        RunItemStreamEvent(
            name="tool_output",
            item=SimpleNamespace(raw_item=SimpleNamespace(call_id="call-1")),
        )
    )

    assert tool_call is not None
    assert tool_result is not None
    assert tool_call.metadata == {"tool_call_id": "call-1", "tool_name": "read_file"}
    assert tool_result.metadata == {"tool_call_id": "call-1"}


def test_stream_tool_events_exclude_non_serializable_sdk_runtime_state() -> None:
    raw_call = {"name": "read_file", "call_id": "call-1", "arguments": '{"path":"a.py"}'}
    tool_call = _visible_event(
        RunItemStreamEvent(
            name="tool_called",
            item=_LockBearingToolCallItem(raw_item=raw_call),
        )
    )
    tool_result = _visible_event(
        RunItemStreamEvent(
            name="tool_output",
            item=_LockBearingToolResultItem(
                raw_item=SimpleNamespace(call_id="call-1"),
                output={"path": "a.py", "content": "bounded"},
            ),
        )
    )

    assert tool_call is not None
    assert tool_result is not None
    assert json.loads(tool_call.content) == raw_call
    assert json.loads(tool_result.content) == {"content": "bounded", "path": "a.py"}

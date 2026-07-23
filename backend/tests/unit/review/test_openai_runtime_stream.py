from types import SimpleNamespace

from agents import RawResponsesStreamEvent

from codelens.review.infrastructure.openai_runtime import _visible_event


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

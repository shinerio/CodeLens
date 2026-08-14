import json

from codelens.review.infrastructure.evidence_replay import (
    CompactedEvidenceReplayRegistry,
)
from codelens.review.infrastructure.openai_runtime import (
    _compact_evidence_tool_results,
    _ContextCompactionTracker,
)


def _items(call_id: str = "call-1") -> list[dict[str, object]]:
    arguments = '{"path":"src/example.py","cursor":null}'
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "get_diff",
            "arguments": arguments,
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": "evidence" * 2000,
        },
    ]


def _compact(
    items: list[dict[str, object]],
    registry: CompactedEvidenceReplayRegistry,
    tracker: _ContextCompactionTracker,
) -> list[dict[str, object]]:
    return _compact_evidence_tool_results(
        items,
        enabled=True,
        trigger_bytes=1024,
        target_bytes=1024,
        keep_recent_evidence_results=0,
        notice="Please reread the exact evidence.",
        tracker=tracker,
        replay_registry=registry,
    )


def test_compaction_placeholder_is_a_complete_v2_tool_result_without_evidence() -> None:
    registry = CompactedEvidenceReplayRegistry()
    tracker = _ContextCompactionTracker()

    compacted = _compact(_items(), registry, tracker)
    placeholder = json.loads(str(compacted[1]["output"]))

    assert placeholder["schema_version"] == "2"
    assert placeholder["tool"] == "get_diff"
    assert placeholder["status"] == "needs_action"
    assert placeholder["data"] == {
        "arguments": {"cursor": None, "path": "src/example.py"},
        "compaction": "codelens_context_compaction_v2",
        "original_bytes": 16000,
        "original_call_id": "call-1",
        "reread_allowed": True,
    }
    assert placeholder["diagnostics"][0]["code"] == "evidence_compacted"
    assert placeholder["diagnostics"][0]["suggested_arguments"] == {
        "cursor": None,
        "path": "src/example.py",
    }
    assert "evidenceevidence" not in str(compacted[1]["output"])


def test_same_call_id_registers_once_but_distinct_calls_register_allowances() -> None:
    registry = CompactedEvidenceReplayRegistry()
    tracker = _ContextCompactionTracker()
    items = _items("call-1")

    _compact(items, registry, tracker)
    _compact(items, registry, tracker)
    _compact(_items("call-2"), registry, tracker)

    assert registry.registered_count == 2
    arguments = '{"cursor":null,"path":"src/example.py"}'
    assert registry.consume("get_diff", arguments)
    assert registry.consume("get_diff", arguments)
    assert not registry.consume("get_diff", arguments)
    assert registry.consumed_count == 2


def test_replay_allowance_requires_exact_evidence_tool_and_arguments() -> None:
    registry = CompactedEvidenceReplayRegistry()
    assert registry.register(
        "call-1", "read_file", '{"path":"src/a.py","version":"current","line_range":null}'
    )

    assert not registry.consume(
        "read_file", '{"path":"src/a.py","version":"base","line_range":null}'
    )
    assert not registry.consume(
        "comment", '{"path":"src/a.py","version":"current","line_range":null}'
    )
    assert registry.consume(
        "read_file", '{"line_range":null,"version":"current","path":"src/a.py"}'
    )

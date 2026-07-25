from datetime import UTC, datetime, timedelta

from codelens.review.application.process_report import ProcessTranscriptEntry, build_process_report


def _entry(
    sequence: int,
    kind: str,
    created_at: datetime,
    *,
    metadata: dict[str, str] | None = None,
) -> ProcessTranscriptEntry:
    return ProcessTranscriptEntry(
        sequence=sequence,
        kind=kind,
        content="",
        created_at=created_at,
        metadata=metadata or {},
    )


def test_process_report_aggregates_llm_tokens_agents_and_tools() -> None:
    started_at = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    agent = "correctness:v1"
    entries = (
        _entry(1, "model_started", started_at, metadata={"agent": agent}),
        _entry(
            2,
            "tool_call",
            started_at + timedelta(seconds=1),
            metadata={"agent": agent, "tool_name": "get_diff", "tool_call_id": "call-1"},
        ),
        _entry(
            3,
            "tool_result",
            started_at + timedelta(seconds=2),
            metadata={"agent": agent, "tool_call_id": "call-1"},
        ),
        _entry(
            4,
            "tool_call",
            started_at + timedelta(seconds=3),
            metadata={"agent": agent, "tool_name": "grep", "tool_call_id": "call-2"},
        ),
        _entry(
            5,
            "tool_result",
            started_at + timedelta(seconds=4),
            metadata={"agent": agent, "tool_call_id": "call-2"},
        ),
        _entry(
            6,
            "model_output",
            started_at + timedelta(seconds=5),
            metadata={
                "agent": agent,
                "model_name": "gpt-5.1",
                "llm_call_count": "3",
                "input_tokens": "120",
                "output_tokens": "30",
                "total_tokens": "150",
            },
        ),
        _entry(7, "model_completed", started_at + timedelta(seconds=6), metadata={"agent": agent}),
    )

    report = build_process_report(
        task_id="review_" + "a" * 32,
        status="completed",
        entries=entries,
        finding_count=2,
    )

    assert report.llm_call_count == 3
    assert (report.input_tokens, report.output_tokens, report.total_tokens) == (120, 30, 150)
    assert report.tool_call_count == report.tool_result_count == 2
    assert [(tool.tool_name, tool.call_count, tool.result_count) for tool in report.tools] == [
        ("get_diff", 1, 1),
        ("grep", 1, 1),
    ]
    assert report.agent_run_count == 1
    assert report.finding_count == 2
    assert report.transcript_entry_count == 7
    assert report.duration_ms == 6_000
    assert report.usage_is_complete
    assert report.agents[0].agent == agent
    assert report.agents[0].model_name == "gpt-5.1"
    assert report.agents[0].duration_ms == 6_000


def test_process_report_marks_usage_incomplete_for_legacy_transcripts() -> None:
    created_at = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    entries = (
        _entry(1, "model_started", created_at, metadata={"agent": "correctness:v1"}),
        _entry(
            2,
            "model_output",
            created_at + timedelta(seconds=1),
            metadata={"agent": "correctness:v1"},
        ),
    )

    report = build_process_report(
        task_id="review_" + "b" * 32,
        status="completed",
        entries=entries,
        finding_count=0,
    )

    assert report.llm_call_count == 0
    assert not report.usage_is_complete

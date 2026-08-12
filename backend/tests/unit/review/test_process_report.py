from datetime import UTC, datetime, timedelta

from codelens.review.application.process_report import ProcessTranscriptEntry, build_process_report


def _entry(
    sequence: int,
    kind: str,
    created_at: datetime,
    *,
    content: str = "",
    metadata: dict[str, str] | None = None,
) -> ProcessTranscriptEntry:
    return ProcessTranscriptEntry(
        sequence=sequence,
        kind=kind,
        content=content,
        created_at=created_at,
        metadata=metadata or {},
    )


def test_process_report_aggregates_llm_tokens_agents_and_tools() -> None:
    started_at = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    agent = "correctness:v2"
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


def test_process_report_distinguishes_rejected_tool_attempts_from_accepted_calls() -> None:
    created_at = datetime(2026, 8, 12, 13, 8, tzinfo=UTC)
    agent = "security:v2"
    entries = (
        _entry(
            1,
            "tool_call",
            created_at,
            metadata={"agent": agent, "tool_name": "comment", "tool_call_id": "call-1"},
        ),
        _entry(
            2,
            "tool_result",
            created_at + timedelta(seconds=1),
            content=(
                '"An error occurred while running the tool. Please try again. Error: '
                'Invalid JSON input for tool comment: extra inputs are not permitted"'
            ),
            metadata={"agent": agent, "tool_call_id": "call-1"},
        ),
        _entry(
            3,
            "tool_call",
            created_at + timedelta(seconds=2),
            metadata={"agent": agent, "tool_name": "comment", "tool_call_id": "call-2"},
        ),
        _entry(
            4,
            "tool_result",
            created_at + timedelta(seconds=3),
            content='"{\\"accepted\\":true,\\"accepted_count\\":1,\\"rejected_count\\":0}"',
            metadata={"agent": agent, "tool_call_id": "call-2"},
        ),
    )

    report = build_process_report(
        task_id="review_" + "e" * 32,
        status="completed",
        entries=entries,
        finding_count=1,
    )

    assert report.tool_call_count == 2
    assert report.accepted_tool_call_count == 1
    assert report.rejected_tool_call_count == 1
    assert report.unclassified_tool_call_count == 0
    assert [
        (
            tool.tool_name,
            tool.call_count,
            tool.accepted_call_count,
            tool.rejected_call_count,
        )
        for tool in report.tools
    ] == [("comment", 2, 1, 1)]
    assert report.rejected_tool_calls[0].reason_code == "invalid_tool_arguments"


def test_process_report_marks_usage_incomplete_for_legacy_transcripts() -> None:
    created_at = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    entries = (
        _entry(1, "model_started", created_at, metadata={"agent": "correctness:v2"}),
        _entry(
            2,
            "model_output",
            created_at + timedelta(seconds=1),
            metadata={"agent": "correctness:v2"},
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


def test_process_report_exposes_live_provider_usage_without_agent_output() -> None:
    created_at = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    agent = "correctness:v2"
    entries = (
        _entry(
            1,
            "model_started",
            created_at,
            metadata={"agent": agent, "response_id": "resp-1", "usage_scope": "provider_call"},
        ),
        _entry(
            2,
            "model_completed",
            created_at + timedelta(seconds=2),
            metadata={
                "agent": agent,
                "response_id": "resp-1",
                "usage_scope": "provider_call",
                "model_name": "gpt-5.1",
                "llm_call_count": "1",
                "input_tokens": "120",
                "cached_input_tokens": "80",
                "cache_write_input_tokens": "10",
                "output_tokens": "30",
                "total_tokens": "150",
            },
        ),
    )

    report = build_process_report(
        task_id="review_" + "f" * 32,
        status="reviewing",
        entries=entries,
        finding_count=0,
    )

    assert report.usage_is_complete
    assert report.llm_call_count == 1
    assert report.input_tokens == 120
    assert report.cached_input_tokens == 80
    assert report.cache_write_input_tokens == 10
    assert report.output_tokens == 30
    assert report.total_tokens == 150
    assert report.duration_ms == 2_000


def test_process_report_does_not_double_count_live_and_terminal_usage() -> None:
    created_at = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    agent = "correctness:v2"
    live_metadata = {
        "agent": agent,
        "response_id": "resp-1",
        "usage_scope": "provider_call",
        "model_name": "gpt-5.1",
        "llm_call_count": "1",
        "input_tokens": "120",
        "cached_input_tokens": "80",
        "cache_write_input_tokens": "10",
        "output_tokens": "30",
        "total_tokens": "150",
    }
    entries = (
        _entry(
            1,
            "model_started",
            created_at,
            metadata={"agent": agent, "usage_scope": "provider_call"},
        ),
        _entry(2, "model_completed", created_at + timedelta(seconds=1), metadata=live_metadata),
        _entry(
            3,
            "model_output",
            created_at + timedelta(seconds=2),
            metadata={
                "agent": agent,
                "usage_scope": "agent_run",
                "model_name": "gpt-5.1",
                "llm_call_count": "1",
                "input_tokens": "120",
                "cached_input_tokens": "80",
                "cache_write_input_tokens": "10",
                "output_tokens": "30",
                "total_tokens": "150",
            },
        ),
    )

    report = build_process_report(
        task_id="review_" + "0" * 32,
        status="completed",
        entries=entries,
        finding_count=0,
    )

    assert report.llm_call_count == 1
    assert report.total_tokens == 150


def test_host_prefetched_review_scope_has_no_tool_transcript_or_usage() -> None:
    created_at = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    entries = (
        _entry(1, "model_started", created_at, metadata={"agent": "correctness:v2"}),
        _entry(
            2,
            "model_output",
            created_at + timedelta(seconds=1),
            metadata={
                "agent": "correctness:v2",
                "model_name": "gpt-5.1",
                "llm_call_count": "1",
                "input_tokens": "10",
                "output_tokens": "2",
                "total_tokens": "12",
            },
        ),
        _entry(
            3,
            "model_completed",
            created_at + timedelta(seconds=2),
            metadata={"agent": "correctness:v2"},
        ),
    )

    report = build_process_report(
        task_id="review_" + "c" * 32,
        status="completed",
        entries=entries,
        finding_count=0,
    )

    assert all(entry.kind not in {"tool_call", "tool_result"} for entry in entries)
    assert report.tool_call_count == 0
    assert report.tool_result_count == 0
    assert report.tools == ()


def test_process_report_marks_retry_usage_incomplete_without_every_attempt_usage() -> None:
    created_at = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    agent = "review-verifier:v2"
    entries = (
        _entry(1, "model_started", created_at, metadata={"agent": agent}),
        _entry(
            2,
            "tool_call",
            created_at + timedelta(seconds=1),
            metadata={"agent": agent, "tool_name": "grep_create_triggered"},
        ),
        _entry(3, "model_started", created_at + timedelta(seconds=2), metadata={"agent": agent}),
        _entry(
            4,
            "model_output",
            created_at + timedelta(seconds=3),
            metadata={
                "agent": agent,
                "model_name": "deepseek-v4-flash",
                "llm_call_count": "8",
                "input_tokens": "417692",
                "cached_input_tokens": "12000",
                "cache_write_input_tokens": "3000",
                "context_compaction_count": "2",
                "context_compacted_result_count": "5",
                "context_compaction_original_bytes": "1000",
                "context_compaction_compressed_bytes": "200",
                "output_tokens": "3267",
                "total_tokens": "420959",
            },
        ),
        _entry(5, "model_completed", created_at + timedelta(seconds=4), metadata={"agent": agent}),
    )

    report = build_process_report(
        task_id="review_" + "d" * 32,
        status="completed",
        entries=entries,
        finding_count=0,
    )

    assert report.agent_run_count == 1
    assert report.usage_is_complete is False
    assert report.cached_input_tokens == 12_000
    assert report.cache_write_input_tokens == 3_000
    assert report.context_compaction_count == 2
    assert report.context_compacted_result_count == 5
    assert report.context_compaction_original_bytes == 1_000
    assert report.context_compaction_compressed_bytes == 200
    assert report.tool_call_count == 0
    assert report.invalid_tool_call_count == 1
    assert [(item.tool_name, item.call_count) for item in report.invalid_tools] == [
        ("grep_create_triggered", 1)
    ]

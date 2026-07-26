"""Exercise one deterministic Review through Git isolation, comment validation, and reporting."""

import asyncio
import json
from pathlib import Path

import pytest

from codelens.bootstrap.settings import Settings
from codelens.bootstrap.unified import build_unified_backend
from codelens.review.application.commands import CreateReviewCommand
from codelens.review.application.process_report import ProcessTranscriptEntry, build_process_report
from codelens.testing.correctness_fixture import (
    FixtureRuntime,
    load_simple_branch_comments,
    prepare_simple_branch_repository,
)
from codelens.workspace.domain.models import BranchScope


@pytest.mark.asyncio
async def test_review_reports_added_deleted_and_modified_defects(tmp_path: Path) -> None:
    fixture = await prepare_simple_branch_repository(tmp_path / "fixture")
    settings = Settings(
        data_dir=tmp_path / "data",
        repository_roots=(fixture.repository,),
    )
    runtime = FixtureRuntime(
        load_simple_branch_comments(),
        repeat_first_comment=True,
    )
    backend = build_unified_backend(settings, runtime=runtime)
    stop_event = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None

    try:
        await backend.start()
        scheduler_task = asyncio.create_task(backend.scheduler.run(stop_event))
        repository = await backend.components.repository_inspector.inspect(fixture.repository)
        review = await backend.components.create_review.handle(
            CreateReviewCommand(
                repository=repository,
                scope=BranchScope(
                    base_ref="main",
                    target_ref="fixture-change",
                    include_workspace_changes=False,
                ),
                selected_agent_versions=("correctness:v1",),
            )
        )

        for _attempt in range(200):
            current = await backend.components.get_review.handle(review.task_id)
            if current.status in {"completed", "partial", "failed", "canceled"}:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("deterministic integration review did not reach a terminal state")

        assert current.status == "completed"
        reviews = await backend.components.list_reviews.handle()
        assert [item.task_id for item in reviews] == [review.task_id]
        assert runtime.calls == 1
        findings = await backend.components.review_store.list_findings(review.task_id)
        assert {
            (finding.primary_location.path, finding.primary_location.side)
            for finding in findings
        } == {
            ("src/cache.py", "new"),
            ("src/permissions.py", "old"),
            ("src/state.py", "new"),
        }
        assert {finding.severity.value for finding in findings} == {
            "critical",
            "high",
            "medium",
        }
        for finding in findings:
            assert finding.title
            assert finding.explanation
            assert finding.recommendation
            assert finding.category
            assert 0.0 <= finding.confidence <= 1.0

        for _attempt in range(100):
            transcript = await backend.components.transcripts.list(review.task_id)
            comment_call = next(
                (
                    entry
                    for entry in transcript
                    if entry.kind == "tool_call"
                    and entry.metadata.get("tool_name") == "comment"
                ),
                None,
            )
            if comment_call is not None:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("terminal Review transcript was not persisted")
        submitted_comments = json.loads(comment_call.content)["comments"]
        assert len(submitted_comments) == 4
        assert submitted_comments[0] == submitted_comments[-1]
        assert all(
            set(comment) == {
                "path",
                "side",
                "existing_code",
                "title",
                "content",
                "recommendation",
                "category",
                "severity",
                "confidence",
            }
            for comment in submitted_comments
        )
        report = build_process_report(
            task_id=review.task_id,
            status=current.status,
            entries=tuple(
                ProcessTranscriptEntry(
                    sequence=entry.sequence,
                    kind=entry.kind,
                    content=entry.content,
                    created_at=entry.created_at,
                    metadata=entry.metadata,
                )
                for entry in transcript
            ),
            finding_count=len(findings),
        )
        assert report.usage_is_complete is True
        assert report.llm_call_count == 2
        assert report.input_tokens == 640
        assert report.output_tokens == 180
        assert report.tool_call_count == report.tool_result_count == 2
        assert report.unmatched_tool_result_count == 0
        assert {
            (tool.tool_name, tool.call_count, tool.result_count) for tool in report.tools
        } == {("comment", 1, 1), ("task_done", 1, 1)}
        assert report.finding_count == 3
    finally:
        stop_event.set()
        if scheduler_task is not None:
            await scheduler_task
        await backend.close()

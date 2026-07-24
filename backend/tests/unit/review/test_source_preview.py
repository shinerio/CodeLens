from pathlib import Path

from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingDisposition,
    FindingSeverity,
    SourceLocation,
)
from codelens.review.application.source_preview import FindingSourcePreviewService
from codelens.review.domain.ports import ReviewExecutionRecord


class Store:
    async def get_execution(self, _task_id: str) -> ReviewExecutionRecord:
        return ReviewExecutionRecord(
            task_id="review_" + "a" * 32,
            repository_path=Path("/repo"),
            repository_realpath_hash="a" * 64,
            git_common_dir_hash="b" * 64,
            base_oid="c" * 40,
            head_oid="d" * 40,
            overlay_hash=None,
            overlay_artifact_ref=None,
            target_paths=("src/example.py",),
            selected_agent_versions=("correctness:v1",),
            prompt_locale="en",
            status="completed",
            cancellation_requested=False,
        )

    async def list_findings(self, _task_id: str) -> tuple[Finding, ...]:
        return (_finding(),)


class Reader:
    async def read_revision(self, repository: Path, revision: str, path: str) -> bytes:
        assert (repository, revision, path) == (Path("/repo"), "d" * 40, "src/example.py")
        return b"one\ntwo\nthree\nfour\nfive\n"


async def test_source_preview_reads_the_pinned_head_revision_and_highlights_finding_lines() -> None:
    preview = await FindingSourcePreviewService(Store(), Reader()).get(
        "review_" + "a" * 32, "finding-1"
    )

    assert preview.path == "src/example.py"
    assert preview.revision == "d" * 40
    assert (preview.start_line, preview.end_line) == (1, 5)
    assert (preview.highlight_start_line, preview.highlight_end_line) == (3, 4)
    assert preview.content == "one\ntwo\nthree\nfour\nfive"


def _finding() -> Finding:
    return Finding(
        finding_id="finding-1",
        fingerprint="f" * 64,
        reviewer_id="correctness",
        category="correctness",
        title="Example",
        severity=FindingSeverity.HIGH,
        disposition=FindingDisposition.BLOCKING,
        confidence=0.9,
        primary_location=SourceLocation("src/example.py", 3, 4, "new", "e" * 64, False),
        related_locations=(),
        changed_hunk_id="hunk-1",
        change_origin=ChangeOrigin.INTRODUCED,
        evidence=(Evidence("excerpt", "proof", None, "e" * 64),),
        impact="impact",
        explanation="explanation",
        reproduction=None,
        recommendation="recommendation",
        suggested_patch=None,
        rule_sources=(),
    )

from pathlib import Path
from typing import Literal

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
            scope_type="branch",
            base_ref="main",
            target_ref="feature",
            overlay_hash=None,
            overlay_artifact_ref=None,
            candidate_paths=("src/example.py",),
            file_exclusion_policy_json=('{"exclude_binary":true,"path_regexes":[],"suffixes":[]}'),
            file_exclusion_policy_hash=(
                "f135f14995e69bb776fd5c18af7fa0d19e45f867501b3274e9cb38cfbc7676c3"
            ),
            selected_agent_versions=("correctness:v2",),
            prompt_locale="en",
            status="completed",
            cancellation_requested=False,
        )

    async def list_findings(self, _task_id: str) -> tuple[Finding, ...]:
        return (_finding(),)


class Reader:
    async def read_revision_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
    ) -> bytes | None:
        assert repository == Path("/repo")
        assert path == "src/example.py"
        if revision == "c" * 40:
            return b"one\ntwo\nold three\nfour\nfive\n"
        if revision == "d" * 40:
            return b"one\ntwo\nthree\nfour\nfive\n"
        raise AssertionError(f"unexpected revision: {revision}")


async def test_source_preview_reads_both_pinned_revisions_and_highlights_finding_side() -> None:
    preview = await FindingSourcePreviewService(Store(), Reader()).get(
        "review_" + "a" * 32, "finding-1"
    )

    assert preview.path == "src/example.py"
    assert preview.base is not None
    assert preview.base.revision == "c" * 40
    assert preview.base.content == "one\ntwo\nold three\nfour\nfive\n"
    assert preview.target is not None
    assert preview.target.revision == "d" * 40
    assert preview.target.content == "one\ntwo\nthree\nfour\nfive\n"
    assert preview.highlight_side == "new"
    assert (preview.highlight_start_line, preview.highlight_end_line) == (3, 4)


async def test_source_preview_returns_complete_file_instead_of_an_excerpt() -> None:
    class FullReader(Reader):
        async def read_revision_optional(
            self,
            repository: Path,
            revision: str,
            path: str,
        ) -> bytes | None:
            assert (repository, path) == (Path("/repo"), "src/example.py")
            return "\n".join(f"line {number}" for number in range(1, 31)).encode()

    preview = await FindingSourcePreviewService(Store(), FullReader()).get(
        "review_" + "a" * 32, "finding-1"
    )

    assert preview.base is not None
    assert preview.target is not None
    assert preview.base.content.startswith("line 1\nline 2")
    assert preview.target.content.endswith("line 30")


async def test_source_preview_allows_a_missing_target_for_a_deleted_file() -> None:
    class DeletedStore(Store):
        async def list_findings(self, _task_id: str) -> tuple[Finding, ...]:
            return (_finding(side="old", is_deleted=True),)

    class DeletedReader(Reader):
        async def read_revision_optional(
            self,
            repository: Path,
            revision: str,
            path: str,
        ) -> bytes | None:
            assert (repository, path) == (Path("/repo"), "src/example.py")
            return b"removed\n" if revision == "c" * 40 else None

    preview = await FindingSourcePreviewService(DeletedStore(), DeletedReader()).get(
        "review_" + "a" * 32, "finding-1"
    )

    assert preview.base is not None
    assert preview.base.content == "removed\n"
    assert preview.target is None
    assert preview.highlight_side == "old"


def _finding(
    *,
    side: Literal["old", "new"] = "new",
    is_deleted: bool = False,
) -> Finding:
    return Finding(
        finding_id="finding-1",
        fingerprint="f" * 64,
        reviewer_id="correctness",
        category="correctness",
        title="Example",
        severity=FindingSeverity.HIGH,
        disposition=FindingDisposition.BLOCKING,
        confidence=0.9,
        primary_location=SourceLocation(
            "src/example.py",
            3,
            4,
            side,
            "e" * 64,
            is_deleted,
        ),
        related_locations=(),
        changed_hunk_id="hunk-1",
        change_origin=ChangeOrigin.INTRODUCED,
        evidence=(Evidence("excerpt", "proof", None, "e" * 64),),
        impact="impact",
        explanation="explanation",
        reproduction=None,
        recommendation="recommendation",
        rule_sources=(),
    )

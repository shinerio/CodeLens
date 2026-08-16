from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

import pytest

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
    visible = True

    async def get_execution(self, _task_id: str) -> ReviewExecutionRecord | None:
        if not self.visible:
            return None
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

    async def resolve_old_path_optional(
        self,
        repository: Path,
        base_revision: str,
        target_revision: str,
        path: str,
    ) -> str | None:
        assert (repository, path) == (Path("/repo"), "src/example.py")
        return None


async def test_source_preview_hides_tombstoned_reviews() -> None:
    store = Store()
    store.visible = False

    with pytest.raises(KeyError):
        await FindingSourcePreviewService(store, Reader()).get(
            "review_" + "a" * 32, "finding-1"
        )


async def test_source_preview_reads_renamed_base_from_old_path() -> None:
    class RenamedReader(Reader):
        async def resolve_old_path_optional(
            self,
            repository: Path,
            base_revision: str,
            target_revision: str,
            path: str,
        ) -> str | None:
            assert path == "src/example.py"
            return "src/old-example.py"

        async def read_revision_optional(
            self,
            repository: Path,
            revision: str,
            path: str,
        ) -> bytes | None:
            assert repository == Path("/repo")
            if revision == "c" * 40:
                assert path == "src/old-example.py"
                return b"old named file\n"
            assert revision == "d" * 40
            assert path == "src/example.py"
            return b"new named file\n"

    preview = await FindingSourcePreviewService(Store(), RenamedReader()).get(
        "review_" + "a" * 32, "finding-1"
    )

    assert preview.base is not None
    assert preview.base.path == "src/old-example.py"
    assert preview.base.content == "old named file\n"
    assert preview.target is not None
    assert preview.target.path == "src/example.py"
    assert preview.target.content == "new named file\n"


async def test_source_preview_reads_target_from_verified_overlay_artifact() -> None:
    class OverlayStore(Store):
        async def get_execution(self, task_id: str) -> ReviewExecutionRecord:
            base = cast(ReviewExecutionRecord, await Store.get_execution(self, task_id))
            return replace(
                base,
                overlay_hash="e" * 64,
                overlay_artifact_ref="input_" + "1" * 32,
            )

    class VerifiedArtifacts:
        async def read_bytes(self, reference: str, expected_hash: str) -> bytes:
            assert reference == "input_" + "1" * 32
            assert expected_hash == "e" * 64
            return b"trusted overlay payload"

    class OverlaySource:
        async def read_overlay_optional(
            self,
            repository: Path,
            revision: str,
            path: str,
            payload: bytes,
        ) -> bytes | None:
            assert (repository, revision, path) == (Path("/repo"), "d" * 40, "src/example.py")
            assert payload == b"trusted overlay payload"
            return b"overlay target\n"

    preview = await FindingSourcePreviewService(
        OverlayStore(), Reader(), VerifiedArtifacts(), OverlaySource()
    ).get("review_" + "a" * 32, "finding-1")

    assert preview.target is not None
    assert preview.target.content == "overlay target\n"


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

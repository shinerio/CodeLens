import hashlib
import json
from pathlib import Path

import pytest

from codelens.review.infrastructure.comment_collector import (
    ReviewCommentCollector,
    ReviewCommentSubmission,
    ReviewCompletionSubmission,
)
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.workspace.domain.models import (
    ChangedHunk,
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotEntry,
    SnapshotManifest,
    TaskWorktree,
)
from codelens.workspace.infrastructure.git_cli import GitCli


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


async def _git(repository: Path, *args: str) -> None:
    result = await GitCli().run(repository, *args)
    assert result.returncode == 0


async def _snapshot(tmp_path: Path) -> ReviewSnapshot:
    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "review@example.test")
    await _git(tmp_path, "config", "user.name", "Review Test")
    source = b"def original() -> str:\n    return 'old'\n"
    helper = b"def helper() -> str:\n    return 'helper'\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_bytes(source)
    (tmp_path / "src" / "helper.py").write_bytes(helper)
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "base")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()

    changed = b"def original() -> str:\n    return 'new'\n"
    (tmp_path / "src" / "service.py").write_bytes(changed)
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "head")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    return ReviewSnapshot(
        snapshot_id="snapshot-1",
        worktree=TaskWorktree("worktree-1", "review-1", "a" * 64, tmp_path, head_oid, "b" * 64),
        target=ReviewTarget(base_oid, head_oid, None),
        fingerprint=RepositoryFingerprint(head_oid, "c" * 64, "d" * 64),
        manifest=SnapshotManifest(
            target_paths=("src/service.py",),
            context_paths=("src/helper.py",),
            excluded_paths=(),
            entries=(
                SnapshotEntry(
                    "src/service.py", "file", 0o100644, len(changed), _hash(changed), None, "target"
                ),
                SnapshotEntry(
                    "src/helper.py", "file", 0o100644, len(helper), _hash(helper), None, "context"
                ),
            ),
        ),
        change_index=ChangeIndex(
            (ChangedHunk("hunk-1", "src/service.py", 2, 2, "new", _hash(b"    return 'new'\n")),)
        ),
    )


async def test_exposes_only_hash_verified_snapshot_content(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    assert json.loads(await tools.explore("src"))["paths"] == ["src/helper.py", "src/service.py"]
    assert json.loads(await tools.glob("src/*.py"))["paths"] == ["src/helper.py", "src/service.py"]
    assert json.loads(await tools.grep("return"))["matches"] == [
        {"line": 2, "path": "src/helper.py", "text": "    return 'helper'"},
        {"line": 2, "path": "src/service.py", "text": "    return 'new'"},
    ]
    read = json.loads(await tools.read_file("src/service.py", 1, 2))
    assert read["content"] == "1|def original() -> str:\n2|    return 'new'"
    assert read["content_hash"] == _hash(b"def original() -> str:\n    return 'new'\n")

    (tmp_path / "src" / "helper.py").write_text("tampered\n")
    with pytest.raises(ValueError, match="changed"):
        await tools.read_file("src/helper.py", 1, 1)
    with pytest.raises(ValueError, match="visible"):
        await tools.read_file(".git/config", 1, 1)


async def test_provides_change_evidence_diff_and_bounded_base_revision_reads(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    change_map = json.loads(await tools.get_change_map())
    assert change_map["hunks"] == [
        {
            "end_line": 2,
            "hunk_id": "hunk-1",
            "path": "src/service.py",
            "side": "new",
            "start_line": 2,
        }
    ]
    assert "-    return 'old'" in json.loads(await tools.get_diff("src/service.py"))["content"]
    assert "+    return 'new'" in json.loads(await tools.get_diff("src/service.py"))["content"]
    base_content = json.loads(await tools.read_revision("src/service.py", "base", 1, 2))["content"]
    assert "2|    return 'old'" in base_content
    with pytest.raises(ValueError, match="revision"):
        await tools.read_revision("src/service.py", "arbitrary", 1, 2)


async def test_rejects_unbounded_tool_use(tmp_path: Path) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=1)

    await tools.explore("src")
    with pytest.raises(ValueError, match="budget"):
        await tools.glob("**/*.py")


async def test_comment_collector_derives_finding_location_from_frozen_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
    )

    acknowledgement = json.loads(
        await collector.submit(
            ReviewCommentSubmission(
                path="src/service.py",
                existing_code="    return 'new'\n",
                title="Missing upgrade migration",
                content="Existing databases do not receive the new field.",
                recommendation="Add an idempotent migration for existing installations.",
                severity="high",
                confidence=0.9,
            )
        )
    )

    finding = collector.finding_batch()["findings"][0]
    assert acknowledgement == {"accepted": True, "comment_count": 1, "hunk_id": "hunk-1"}
    assert finding["changed_hunk_id"] == "hunk-1"
    assert finding["primary_location"]["excerpt_hash"] == _hash(b"    return 'new'\n")
    assert finding["reviewer_id"] == "correctness"


async def test_comment_collector_rejects_location_outside_changed_hunk(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
    )

    with pytest.raises(ValueError, match="changed new-side hunk"):
        await collector.submit(
            ReviewCommentSubmission(
                path="src/service.py",
                existing_code="def original() -> str:\n",
                title="Unchanged location",
                content="This is outside the changed hunk.",
                recommendation="Do not report this location.",
                confidence=0.9,
            )
        )
    assert collector.finding_batch() == {"schema_version": "1", "findings": ()}


async def test_chinese_comment_collector_rejects_english_finding_text(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
        output_locale="zh-CN",
    )

    with pytest.raises(ValueError, match="Chinese"):
        await collector.submit(
            ReviewCommentSubmission(
                path="src/service.py",
                existing_code="    return 'new'\n",
                title="Missing migration",
                content="Existing databases cannot read the new column.",
                recommendation="Add an ALTER TABLE statement.",
                confidence=0.9,
            )
        )


async def test_comment_collector_accepts_batch_and_completion_declaration(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
    )

    acknowledgement = json.loads(
        await collector.submit_many(
            [
                ReviewCommentSubmission(
                    path="src/service.py",
                    existing_code="    return 'new'\n",
                    title="Concurrent upgrade issue",
                    content="The new path misses existing installations.",
                    recommendation="Add an idempotent migration.",
                    confidence=0.9,
                )
            ]
        )
    )
    completion = json.loads(
        collector.complete(
            ReviewCompletionSubmission(
                summary="Reviewed the changed service file.",
                reviewed_changed_files=1,
            )
        )
    )

    assert acknowledgement == {
        "accepted": True,
        "accepted_count": 1,
        "comment_count": 1,
        "hunk_ids": ["hunk-1"],
    }
    assert completion == {"accepted": True, "comment_count": 1, "reviewed_changed_files": 1}
    assert collector.is_completed is True
    with pytest.raises(ValueError, match="already"):
        collector.complete(
            ReviewCompletionSubmission(summary="Repeated completion.", reviewed_changed_files=1)
        )

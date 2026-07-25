import hashlib
import json
from dataclasses import replace
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
    ReviewFileChange,
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
    root_rules = b"Follow repository-wide rules.\n"
    root_review_rules = b"Review all public contracts.\n"
    source_rules = b"Review source compatibility.\n"
    file_rules = b"Check service migrations.\n"
    unrelated_rules = b"Only applies to tests.\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "service.py").write_bytes(source)
    (tmp_path / "src" / "helper.py").write_bytes(helper)
    (tmp_path / "AGENTS.md").write_bytes(root_rules)
    (tmp_path / "REVIEW.md").write_bytes(root_review_rules)
    (tmp_path / "src" / "REVIEW.md").write_bytes(source_rules)
    (tmp_path / "src" / "service.py.review.md").write_bytes(file_rules)
    (tmp_path / "tests" / "REVIEW.md").write_bytes(unrelated_rules)
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
            instruction_paths=(
                "AGENTS.md",
                "REVIEW.md",
                "src/REVIEW.md",
                "src/service.py.review.md",
                "tests/REVIEW.md",
            ),
            excluded_paths=(),
            entries=(
                SnapshotEntry(
                    "src/service.py", "file", 0o100644, len(changed), _hash(changed), None, "target"
                ),
                SnapshotEntry(
                    "src/helper.py", "file", 0o100644, len(helper), _hash(helper), None, "context"
                ),
                SnapshotEntry(
                    "AGENTS.md",
                    "file",
                    0o100644,
                    len(root_rules),
                    _hash(root_rules),
                    None,
                    "instruction",
                ),
                SnapshotEntry(
                    "REVIEW.md",
                    "file",
                    0o100644,
                    len(root_review_rules),
                    _hash(root_review_rules),
                    None,
                    "instruction",
                ),
                SnapshotEntry(
                    "src/REVIEW.md",
                    "file",
                    0o100644,
                    len(source_rules),
                    _hash(source_rules),
                    None,
                    "instruction",
                ),
                SnapshotEntry(
                    "src/service.py.review.md",
                    "file",
                    0o100644,
                    len(file_rules),
                    _hash(file_rules),
                    None,
                    "instruction",
                ),
                SnapshotEntry(
                    "tests/REVIEW.md",
                    "file",
                    0o100644,
                    len(unrelated_rules),
                    _hash(unrelated_rules),
                    None,
                    "instruction",
                ),
            ),
        ),
        change_index=ChangeIndex(
            hunks=(
                ChangedHunk(
                    "hunk-1",
                    "src/service.py",
                    2,
                    2,
                    "new",
                    _hash(b"    return 'new'\n"),
                ),
            ),
            files=(ReviewFileChange("src/service.py", "modified"),),
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
    assert "content_hash" not in read

    (tmp_path / "src" / "helper.py").write_text("tampered\n")
    with pytest.raises(ValueError, match="changed"):
        await tools.read_file("src/helper.py", 1, 1)
    with pytest.raises(ValueError, match="visible"):
        await tools.read_file(".git/config", 1, 1)


async def test_provides_diff_and_bounded_base_revision_reads(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    diff = json.loads(await tools.get_diff("src/service.py"))
    assert "-    return 'old'" in diff["content"]
    assert "+    return 'new'" in diff["content"]
    assert "content_hash" not in diff
    revision = json.loads(await tools.read_revision("src/service.py", "base", 1, 2))
    base_content = revision["content"]
    assert "2|    return 'old'" in base_content
    assert "content_hash" not in revision
    with pytest.raises(ValueError, match="revision"):
        await tools.read_revision("src/service.py", "arbitrary", 1, 2)


async def test_get_diff_accepts_review_files_without_hunks(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    snapshot = replace(snapshot, change_index=replace(snapshot.change_index, hunks=()))

    diff = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(
            "src/service.py"
        )
    )

    assert "+    return 'new'" in diff["content"]


async def test_get_diff_accepts_pure_rename_without_text_hunks(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    base_oid = snapshot.target.head_oid
    await _git(tmp_path, "mv", "src/service.py", "src/renamed.py")
    await _git(tmp_path, "commit", "-m", "rename service")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    payload = (tmp_path / "src" / "renamed.py").read_bytes()
    entry = SnapshotEntry(
        "src/renamed.py",
        "file",
        0o644,
        len(payload),
        _hash(payload),
        None,
        "target",
    )
    snapshot = replace(
        snapshot,
        worktree=replace(snapshot.worktree, head_oid=head_oid),
        target=ReviewTarget(base_oid, head_oid, None),
        manifest=replace(
            snapshot.manifest,
            target_paths=("src/renamed.py",),
            entries=(*snapshot.manifest.entries, entry),
        ),
        change_index=ChangeIndex(
            hunks=(),
            files=(
                ReviewFileChange(
                    "src/renamed.py",
                    "renamed",
                    old_path="src/service.py",
                ),
            ),
        ),
    )

    diff = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(
            "src/renamed.py"
        )
    )

    assert "rename from src/service.py" in diff["content"]
    assert "rename to src/renamed.py" in diff["content"]


async def test_get_diff_accepts_binary_change_without_text_hunks(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/payload.bin"
    (tmp_path / path).write_bytes(b"old\0payload")
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "add binary")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    payload = b"new\0payload"
    (tmp_path / path).write_bytes(payload)
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "change binary")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    entry = SnapshotEntry(path, "file", 0o644, len(payload), _hash(payload), None, "target")
    snapshot = replace(
        snapshot,
        worktree=replace(snapshot.worktree, head_oid=head_oid),
        target=ReviewTarget(base_oid, head_oid, None),
        manifest=replace(
            snapshot.manifest,
            target_paths=(path,),
            entries=(*snapshot.manifest.entries, entry),
        ),
        change_index=ChangeIndex(hunks=(), files=(ReviewFileChange(path, "modified"),)),
    )

    diff = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(path)
    )

    assert "Binary files" in diff["content"]


async def test_get_diff_synthesizes_untracked_added_file_from_frozen_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/new.py"
    payload = b"first = 1\nsecond = 2\n"
    (tmp_path / path).write_bytes(payload)
    entry = SnapshotEntry(path, "file", 0o644, len(payload), _hash(payload), None, "target")
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            target_paths=(path,),
            entries=(*snapshot.manifest.entries, entry),
        ),
        change_index=ChangeIndex(
            hunks=(ChangedHunk("new-hunk", path, 1, 2, "new", _hash(payload)),),
            files=(ReviewFileChange(path, "added"),),
        ),
    )

    diff = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(path)
    )

    assert "--- /dev/null" in diff["content"]
    assert "+++ b/src/new.py" in diff["content"]
    assert "+first = 1" in diff["content"]
    assert "+second = 2" in diff["content"]


async def test_snapshot_symlink_payload_uses_frozen_link_target_text(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/link.py"
    target = "service.py"
    (tmp_path / path).symlink_to(target)
    payload = target.encode("utf-8")
    entry = SnapshotEntry(path, "symlink", 0o777, len(payload), _hash(payload), target, "target")
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            target_paths=(path,),
            entries=(*snapshot.manifest.entries, entry),
        ),
        change_index=ChangeIndex(
            hunks=(ChangedHunk("link-hunk", path, 1, 1, "new", _hash(payload)),),
            files=(ReviewFileChange(path, "added"),),
        ),
    )

    result = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).read_file(path, 1, 1)
    )

    assert result["content"] == "1|service.py"


async def test_instruction_loader_returns_only_hash_verified_rules_for_complete_target_path(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    initial = json.loads(await tools.initial_instruction_context())
    result = json.loads(await tools.instruction_loader("src/service.py"))

    assert initial == {
        "available_instruction_paths": [
            "src/REVIEW.md",
            "src/service.py.review.md",
        ],
        "root_instructions": [
            {"content": "Follow repository-wide rules.\n", "path": "AGENTS.md"},
            {"content": "Review all public contracts.\n", "path": "REVIEW.md"},
        ],
    }
    assert result == {
        "new_instructions": [
            {"content": "Review source compatibility.\n", "path": "src/REVIEW.md"},
            {"content": "Check service migrations.\n", "path": "src/service.py.review.md"},
        ],
        "path": "src/service.py",
        "reused_instruction_paths": ["AGENTS.md", "REVIEW.md"],
        "rule_paths": [
            "AGENTS.md",
            "REVIEW.md",
            "src/REVIEW.md",
            "src/service.py.review.md",
        ],
    }
    assert tools.instructions_loaded_for("src/service.py") is True
    with pytest.raises(ValueError, match="complete repository-relative target path"):
        await tools.instruction_loader("/src/service.py")
    with pytest.raises(ValueError, match="complete repository-relative target path"):
        await tools.instruction_loader("../src/service.py")
    with pytest.raises(ValueError, match="complete repository-relative target path"):
        await tools.instruction_loader("src/helper.py")

    nested_tampered_tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)
    await nested_tampered_tools.initial_instruction_context()
    (tmp_path / "src" / "REVIEW.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        await nested_tampered_tools.instruction_loader("src/service.py")

    tampered_tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)
    (tmp_path / "AGENTS.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        await tampered_tools.initial_instruction_context()


async def test_instruction_loader_reuses_rule_bodies_for_targets_in_the_same_directory(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            target_paths=("src/service.py", "src/helper.py"),
            context_paths=(),
            entries=tuple(
                replace(entry, origin="target") if entry.path == "src/helper.py" else entry
                for entry in snapshot.manifest.entries
            ),
        ),
        change_index=replace(
            snapshot.change_index,
            files=(
                *snapshot.change_index.files,
                ReviewFileChange("src/helper.py", "modified"),
            ),
        ),
    )
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    await tools.initial_instruction_context()
    await tools.instruction_loader("src/service.py")
    result = json.loads(await tools.instruction_loader("src/helper.py"))

    assert result == {
        "new_instructions": [],
        "path": "src/helper.py",
        "reused_instruction_paths": ["AGENTS.md", "REVIEW.md", "src/REVIEW.md"],
        "rule_paths": ["AGENTS.md", "REVIEW.md", "src/REVIEW.md"],
    }


async def test_rejects_unbounded_tool_use(tmp_path: Path) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=1)

    await tools.explore("src")
    with pytest.raises(ValueError, match="budget"):
        await tools.glob("**/*.py")


async def test_initial_instruction_prefetch_does_not_consume_tool_budget(tmp_path: Path) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=1)

    await tools.initial_instruction_context()
    await tools.instruction_loader("src/service.py")

    with pytest.raises(ValueError, match="budget"):
        await tools.explore("src")


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
    await collector.tools.instruction_loader("src/service.py")

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
    assert acknowledgement == {"accepted": True, "comment_count": 1}
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
    await collector.tools.instruction_loader("src/service.py")

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


async def test_comment_collector_accepts_batch_and_completion_declaration(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
    )
    await collector.tools.instruction_loader("src/service.py")

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
    }
    assert completion == {"accepted": True, "comment_count": 1, "reviewed_changed_files": 1}
    assert collector.is_completed is True
    with pytest.raises(ValueError, match="already"):
        collector.complete(
            ReviewCompletionSubmission(summary="Repeated completion.", reviewed_changed_files=1)
        )


async def test_comment_collector_requires_instruction_loader_before_output(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
    )

    with pytest.raises(ValueError, match="instruction_loader"):
        await collector.submit(
            ReviewCommentSubmission(
                path="src/service.py",
                existing_code="    return 'new'\n",
                title="Missing upgrade migration",
                content="Existing databases do not receive the new field.",
                recommendation="Add an idempotent migration.",
                confidence=0.9,
            )
        )
    with pytest.raises(ValueError, match="instruction_loader"):
        collector.complete(
            ReviewCompletionSubmission(
                summary="Reviewed the changed service file.",
                reviewed_changed_files=1,
            )
        )

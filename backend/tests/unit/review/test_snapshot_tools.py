import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents import RunConfig, Usage
from agents.tool_context import ToolContext

from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.comment_collector import (
    ReviewCommentCollector,
    ReviewCommentSubmission,
    ReviewCompletionSubmission,
    ReviewFileCompletionSubmission,
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
    await _git(tmp_path, "config", "commit.gpgSign", "false")
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
                    "old-hunk",
                    "src/service.py",
                    2,
                    2,
                    "old",
                    _hash(b"    return 'old'\n"),
                ),
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

    assert json.loads(await tools.find_files(path="src"))["paths"] == [
        "src/helper.py",
        "src/service.py",
    ]
    assert json.loads(await tools.find_files(pattern="src/*.py"))["paths"] == [
        "src/helper.py",
        "src/service.py",
    ]
    assert json.loads(await tools.find_files(path="src", pattern="service.*"))["paths"] == [
        "src/service.py"
    ]
    assert json.loads(await tools.grep("return"))["matches"] == [
        {"line": 2, "path": "src/helper.py", "text": "    return 'helper'"},
        {"line": 2, "path": "src/service.py", "text": "    return 'new'"},
    ]
    read = json.loads(await tools.read_file("src/service.py", 1, 2))
    assert read["content"] == "1|def original() -> str:\n2|    return 'new'"
    assert read["version"] == "current"
    assert "content_hash" not in read

    (tmp_path / "src" / "helper.py").write_text("tampered\n")
    with pytest.raises(ValueError, match="changed"):
        await tools.read_file("src/helper.py", 1, 1)
    with pytest.raises(ValueError, match="visible"):
        await tools.read_file(".git/config", 1, 1)


async def test_grep_reports_truncation_and_supports_narrower_retry(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    custom_limits = ToolLimits(max_scan_bytes=45)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20, tool_limits=custom_limits)

    broad_result = json.loads(await tools.grep("return", path="src"))
    assert broad_result == {
        "matches": [
            {"line": 2, "path": "src/helper.py", "text": "    return 'helper'"}
        ],
        "truncated": True,
    }

    narrower_result = json.loads(
        await tools.grep("return", path="src", file_pattern="service.*")
    )
    assert narrower_result == {
        "matches": [
            {"line": 2, "path": "src/service.py", "text": "    return 'new'"}
        ],
        "truncated": False,
    }

    outside_scope = json.loads(await tools.grep("return", path="tests"))
    assert outside_scope == {"matches": [], "truncated": False}


async def test_grep_filters_directory_scope_with_relative_file_pattern(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    result = json.loads(
        await tools.grep(
            "return",
            path="src",
            file_pattern="service.*",
        )
    )

    assert result["matches"] == [
        {"line": 2, "path": "src/service.py", "text": "    return 'new'"}
    ]
    exact_file = json.loads(
        await tools.grep(
            "return",
            path="src/service.py",
            file_pattern="*.py",
        )
    )
    assert exact_file["matches"] == result["matches"]
    excluded_exact_file = json.loads(
        await tools.grep(
            "return",
            path="src/service.py",
            file_pattern="*.json",
        )
    )
    assert excluded_exact_file["matches"] == []
    with pytest.raises(ValueError, match="file pattern"):
        await tools.grep("return", path="src", file_pattern="../*.py")


async def test_grep_returns_a_window_containing_the_actual_match(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/helper.py"
    payload = f"{'prefix-' * 50}needle{'-suffix' * 50}\n".encode()
    (tmp_path / path).write_bytes(payload)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            entries=tuple(
                replace(entry, size_bytes=len(payload), content_hash=_hash(payload))
                if entry.path == path
                else entry
                for entry in snapshot.manifest.entries
            ),
        ),
    )

    result = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).grep(
            "needle", path=path, file_pattern="*.py"
        )
    )

    assert len(result["matches"]) == 1
    assert "needle" in result["matches"][0]["text"]
    assert len(result["matches"][0]["text"]) <= 200


@pytest.mark.parametrize(("match_count", "is_truncated"), [(200, False), (201, True)])
async def test_grep_truncates_only_when_additional_matches_exist(
    tmp_path: Path,
    match_count: int,
    is_truncated: bool,
) -> None:
    snapshot = await _snapshot(tmp_path)
    payload = b"match\n" * match_count
    path = "src/helper.py"
    (tmp_path / path).write_bytes(payload)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            entries=tuple(
                replace(
                    entry,
                    size_bytes=len(payload),
                    content_hash=_hash(payload),
                )
                if entry.path == path
                else entry
                for entry in snapshot.manifest.entries
            ),
        ),
    )
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    result = json.loads(await tools.grep("match", path=path, file_pattern="*.py"))

    assert len(result["matches"]) == min(match_count, 200)
    assert result["truncated"] is is_truncated


async def test_find_files_uses_posix_path_glob_semantics(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    nested_path = "src/nested/worker.py"
    nested_payload = b"VALUE = 1\n"
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / nested_path).write_bytes(nested_payload)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            context_paths=(*snapshot.manifest.context_paths, nested_path),
            entries=(
                *snapshot.manifest.entries,
                SnapshotEntry(
                    nested_path,
                    "file",
                    0o644,
                    len(nested_payload),
                    _hash(nested_payload),
                    None,
                    "context",
                ),
            ),
        ),
    )
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    direct = json.loads(await tools.find_files(path="src", pattern="*.py"))
    recursive = json.loads(await tools.find_files(path="src", pattern="**/*.py"))

    assert direct["paths"] == ["src/helper.py", "src/service.py"]
    assert recursive["paths"] == [
        "src/helper.py",
        "src/nested/worker.py",
        "src/service.py",
    ]


async def test_find_files_reports_non_paginated_truncation(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    custom_limits = ToolLimits(max_results=1)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20, tool_limits=custom_limits)

    result = json.loads(await tools.find_files(path="src", pattern="*.py"))

    assert result == {"paths": ["src/helper.py"], "truncated": True}


async def test_read_file_supports_bounded_whole_file_mode(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    read = json.loads(await tools.read_file("src/service.py"))

    assert read == {
        "path": "src/service.py",
        "version": "current",
        "start_line": 1,
        "end_line": 2,
        "content": "1|def original() -> str:\n2|    return 'new'",
        "truncated": False,
    }
    with pytest.raises(ValueError, match="provided together"):
        await tools.read_file("src/service.py", start_line=1)


async def test_read_file_marks_whole_file_line_limit_as_truncated(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    custom_limits = ToolLimits(max_lines=1)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20, tool_limits=custom_limits)

    read = json.loads(await tools.read_file("src/service.py"))

    assert read["content"] == "1|def original() -> str:"
    assert read["end_line"] == 1
    assert read["truncated"] is True


async def test_tools_reject_oversized_snapshot_sources_before_reading(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/helper.py"
    payload = b"0123456789abcdef\n"
    (tmp_path / path).write_bytes(payload)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            entries=tuple(
                replace(entry, size_bytes=len(payload), content_hash=_hash(payload))
                if entry.path == path
                else entry
                for entry in snapshot.manifest.entries
            ),
        ),
    )
    custom_limits = ToolLimits(max_source_bytes=8)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20, tool_limits=custom_limits)

    with pytest.raises(ValueError, match="source file exceeds"):
        await tools.read_file(path)


async def test_provides_diff_and_bounded_base_version_reads(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    diff = json.loads(await tools.get_diff("src/service.py"))
    assert "-    return 'old'" in diff["content"]
    assert "+    return 'new'" in diff["content"]
    assert "content_hash" not in diff
    revision = json.loads(await tools.read_file("src/service.py", 1, 2, "base"))
    base_content = revision["content"]
    assert "2|    return 'old'" in base_content
    assert "content_hash" not in revision
    head = json.loads(await tools.read_file("src/service.py", 1, 2, "head"))
    assert head["version"] == "head"
    assert "2|    return 'new'" in head["content"]
    with pytest.raises(ValueError, match="version"):
        await tools.read_file("src/service.py", 1, 2, "arbitrary")


async def test_get_diff_separates_lines_without_trailing_newlines(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/no-newline.py"
    (tmp_path / path).write_bytes(b"old")
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "add no-newline source")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    payload = b"new"
    (tmp_path / path).write_bytes(payload)
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "change no-newline source")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    snapshot = replace(
        snapshot,
        worktree=replace(snapshot.worktree, head_oid=head_oid),
        target=ReviewTarget(base_oid, head_oid, None),
        manifest=replace(
            snapshot.manifest,
            target_paths=(path,),
            entries=(
                *snapshot.manifest.entries,
                SnapshotEntry(path, "file", 0o644, len(payload), _hash(payload), None, "target"),
            ),
        ),
        change_index=ChangeIndex(
            hunks=(
                ChangedHunk("old-no-newline", path, 1, 1, "old", _hash(b"old")),
                ChangedHunk("new-no-newline", path, 1, 1, "new", _hash(b"new")),
            ),
            files=(ReviewFileChange(path, "modified"),),
        ),
    )

    diff = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(path)
    )["content"]

    assert "-old\n\\ No newline at end of file\n" in diff
    assert "+new\n\\ No newline at end of file\n" in diff


async def test_get_diff_rejects_content_changed_after_snapshot(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    (tmp_path / "src" / "service.py").write_text(
        "def original() -> str:\n    return 'tampered'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="changed"):
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(
            "src/service.py"
        )


async def test_get_diff_compares_base_with_verified_current_overlay(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    overlay = b"def original() -> str:\n    return 'overlay'\n"
    (tmp_path / "src" / "service.py").write_bytes(overlay)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            entries=tuple(
                replace(entry, size_bytes=len(overlay), content_hash=_hash(overlay))
                if entry.path == "src/service.py"
                else entry
                for entry in snapshot.manifest.entries
            ),
        ),
    )

    diff = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(
            "src/service.py"
        )
    )

    assert "+    return 'overlay'" in diff["content"]
    assert "+    return 'new'" not in diff["content"]


async def test_deleted_snapshot_entry_rejects_recreated_path(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    deleted_path = "src/deleted.py"
    recreated = b"VALUE = 'recreated'\n"
    (tmp_path / deleted_path).write_bytes(recreated)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            target_paths=(deleted_path,),
            entries=(
                *snapshot.manifest.entries,
                SnapshotEntry(deleted_path, "deleted", 0, 0, _hash(b""), None, "target"),
            ),
        ),
        change_index=ChangeIndex(
            hunks=(),
            files=(ReviewFileChange(deleted_path, "deleted"),),
        ),
    )

    with pytest.raises(ValueError, match="changed"):
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).read_file(
            deleted_path, 1, 1, "current"
        )


async def test_grep_times_out_catastrophic_regular_expression(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    payload = ("a" * 28 + "!\n").encode()
    (tmp_path / "src" / "service.py").write_bytes(payload)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            entries=tuple(
                replace(entry, size_bytes=len(payload), content_hash=_hash(payload))
                if entry.path == "src/service.py"
                else entry
                for entry in snapshot.manifest.entries
            ),
        ),
    )
    custom_limits = ToolLimits(regex_timeout_seconds=0.05)
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=custom_limits,
    )

    with pytest.raises(ValueError, match="timed out"):
        await tools.grep(r"(a+)+$")


async def test_grep_timeout_starts_after_isolated_worker_is_ready(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    custom_limits = ToolLimits(regex_timeout_seconds=0.05)
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=custom_limits,
    )

    result = json.loads(await tools.grep("return"))

    assert len(result["matches"]) == 2


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
    base = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).read_file(
            "src/renamed.py", 1, 2, "base"
        )
    )
    assert base["path"] == "src/renamed.py"
    assert base["version"] == "base"
    assert "2|    return 'new'" in base["content"]


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


async def test_rejects_unbounded_tool_use(tmp_path: Path) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=1)

    await tools.find_files(path="src")
    with pytest.raises(ValueError, match="budget"):
        await tools.find_files(pattern="**/*.py")


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
                side="new",
                existing_code="    return 'new'\n",
                title="Missing upgrade migration",
                content="Existing databases do not receive the new field.",
                recommendation="Add an idempotent migration for existing installations.",
                category="correctness",
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


async def test_comment_collector_resolves_a_base_side_deleted_line(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
    )

    await collector.submit(
        ReviewCommentSubmission(
            path="src/service.py",
            side="old",
            existing_code="    return 'old'\n",
            title="Removed fallback",
            content="The target revision removes the required fallback.",
            recommendation="Keep the fallback in the target revision.",
            category="correctness",
            severity="high",
            confidence=0.9,
        )
    )

    finding = collector.finding_batch()["findings"][0]
    assert finding["changed_hunk_id"] == "old-hunk"
    assert finding["primary_location"] == {
        "path": "src/service.py",
        "start_line": 2,
        "end_line": 2,
        "side": "old",
        "excerpt_hash": _hash(b"    return 'old'\n"),
        "is_deleted": False,
    }


async def test_comment_collector_marks_a_deleted_file_location(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    (tmp_path / "src" / "service.py").unlink()
    await _git(tmp_path, "add", "-A")
    await _git(tmp_path, "commit", "-m", "delete service")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    deleted_snapshot = replace(
        snapshot,
        worktree=replace(snapshot.worktree, head_oid=head_oid),
        target=replace(snapshot.target, head_oid=head_oid),
        manifest=replace(
            snapshot.manifest,
            entries=(
                SnapshotEntry(
                    "src/service.py",
                    "deleted",
                    0,
                    0,
                    _hash(b""),
                    None,
                    "target",
                ),
                *snapshot.manifest.entries[1:],
            ),
        ),
        change_index=ChangeIndex(
            hunks=(
                ChangedHunk(
                    "deleted-file-hunk",
                    "src/service.py",
                    1,
                    2,
                    "old",
                    _hash(b"def original() -> str:\n    return 'old'\n"),
                ),
            ),
            files=(ReviewFileChange("src/service.py", "deleted"),),
        ),
    )
    collector = ReviewCommentCollector(
        snapshot=deleted_snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(deleted_snapshot, GitCli(), max_tool_calls=20),
    )

    await collector.submit(
        ReviewCommentSubmission(
            path="src/service.py",
            side="old",
            existing_code="    return 'old'\n",
            title="Deleted required service",
            content="The target revision removes a required service implementation.",
            recommendation="Restore the service implementation.",
            category="correctness",
            severity="high",
            confidence=0.9,
        )
    )

    finding = collector.finding_batch()["findings"][0]
    assert finding["changed_hunk_id"] == "deleted-file-hunk"
    assert finding["primary_location"] == {
        "path": "src/service.py",
        "start_line": 2,
        "end_line": 2,
        "side": "old",
        "excerpt_hash": _hash(b"    return 'old'\n"),
        "is_deleted": True,
    }


async def test_comment_collector_accepts_location_outside_changed_hunk(tmp_path: Path) -> None:
    """Comments quoting unchanged context lines within a changed file are accepted.

    The hunk containment check was relaxed: as long as the file is in the
    review scope and existing_code resolves to a line range, the comment is
    accepted with changed_hunk_id=None when the range falls outside a single
    --unified=0 hunk.
    """
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
    )
    result = await collector.submit(
        ReviewCommentSubmission(
            path="src/service.py",
            side="new",
            existing_code="def original() -> str:\n",
            title="Unchanged location",
            content="This is outside the changed hunk.",
            recommendation="Do not report this location.",
            category="correctness",
            severity="medium",
            confidence=0.9,
        )
    )
    assert json.loads(result)["accepted"] is True
    batch = collector.finding_batch()
    assert len(batch["findings"]) == 1
    assert batch["findings"][0]["changed_hunk_id"] is None


async def test_comment_collector_accepts_batch_and_completion_declaration(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=tools,
        max_incomplete_review_retries=2,
    )
    acknowledgement = json.loads(
        await collector.submit_many(
            [
                ReviewCommentSubmission(
                    path="src/service.py",
                    side="new",
                    existing_code="    return 'new'\n",
                    title="Concurrent upgrade issue",
                    content="The new path misses existing installations.",
                    recommendation="Add an idempotent migration.",
                    category="correctness",
                    severity="medium",
                    confidence=0.9,
                )
            ]
        )
    )
    missing_evidence = json.loads(
        collector.complete_files(
            ReviewFileCompletionSubmission(reviewed_files=("src/service.py",))
        )
    )
    await tools.get_diff("src/service.py")
    file_completion = json.loads(
        collector.complete_files(
            ReviewFileCompletionSubmission(reviewed_files=("src/service.py",))
        )
    )
    completion = json.loads(
        collector.complete(ReviewCompletionSubmission(summary="Reviewed the changed service file."))
    )

    assert acknowledgement == {
        "accepted": True,
        "accepted_count": 1,
        "comment_count": 1,
        "rejected_comments": [],
        "rejected_count": 0,
    }
    assert missing_evidence == {
        "accepted": False,
        "missing_evidence_files": ["src/service.py"],
        "recorded_files": [],
    }
    assert file_completion == {
        "accepted": True,
        "missing_evidence_files": [],
        "recorded_files": ["src/service.py"],
    }
    assert completion == {
        "accepted": True,
        "comment_count": 1,
        "forced_completion": False,
        "reviewed_files": ["src/service.py"],
    }
    assert collector.is_completed is True
    with pytest.raises(ValueError, match="already"):
        collector.complete(ReviewCompletionSubmission(summary="Repeated completion."))


async def test_comment_collector_rejects_only_invalid_candidates_in_a_batch(
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
        await collector.submit_many(
            [
                ReviewCommentSubmission(
                    path="src/service.py",
                    side="old",
                    existing_code="    return 'old'\n",
                    title="Removed fallback",
                    content="The required fallback is removed.",
                    recommendation="Keep the fallback.",
                    category="correctness",
                    severity="high",
                    confidence=0.9,
                ),
                ReviewCommentSubmission(
                    path="src/service.py",
                    side="new",
                    existing_code="    return 'new'\n",
                    title="Low-confidence candidate",
                    content="This candidate is too uncertain.",
                    recommendation="Investigate further.",
                    category="correctness",
                    severity="low",
                    confidence=0.2,
                ),
                ReviewCommentSubmission(
                    path="src/helper.py",
                    side="new",
                    existing_code="HELPER = True\n",
                    title="Out-of-scope candidate",
                    content="This path is not a changed Review file.",
                    recommendation="Submit only changed Review paths.",
                    category="correctness",
                    severity="low",
                    confidence=0.9,
                ),
                ReviewCommentSubmission(
                    path="src/service.py",
                    side="new",
                    existing_code="    return 'new'\n",
                    title="Missing migration",
                    content="Existing installations do not receive the new state.",
                    recommendation="Add an idempotent migration.",
                    category="correctness",
                    severity="medium",
                    confidence=0.9,
                ),
            ]
        )
    )

    assert acknowledgement == {
        "accepted": True,
        "accepted_count": 2,
        "comment_count": 2,
        "rejected_comments": [
            {
                "index": 1,
                "reason": "comment confidence is below this reviewer's threshold",
            },
            {"index": 2, "reason": "comment path is outside this Review"},
        ],
        "rejected_count": 2,
    }
    assert [finding["title"] for finding in collector.finding_batch()["findings"]] == [
        "Removed fallback",
        "Missing migration",
    ]


async def test_completion_rejection_distinguishes_unread_and_undeclared_files(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=tools,
        max_incomplete_review_retries=2,
    )

    unread = json.loads(
        collector.complete(ReviewCompletionSubmission(summary="Attempted completion."))
    )
    await tools.read_file("src/service.py", 1, 2, "current")
    undeclared = json.loads(
        collector.complete(ReviewCompletionSubmission(summary="Attempted completion again."))
    )
    forced = json.loads(
        collector.complete(ReviewCompletionSubmission(summary="Retry limit reached."))
    )

    assert unread == {
        "accepted": False,
        "incomplete_retry_count": 1,
        "max_incomplete_review_retries": 2,
        "missing_evidence_files": ["src/service.py"],
        "undeclared_files": [],
    }
    assert undeclared == {
        "accepted": False,
        "incomplete_retry_count": 2,
        "max_incomplete_review_retries": 2,
        "missing_evidence_files": [],
        "undeclared_files": ["src/service.py"],
    }
    assert forced == {
        "accepted": True,
        "comment_count": 0,
        "forced_completion": True,
        "incomplete_files": ["src/service.py"],
        "reviewed_files": [],
    }
    assert collector.incomplete_review_files == ("src/service.py",)


async def test_zero_completion_retries_accepts_first_incomplete_attempt_with_warning(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
        max_incomplete_review_retries=0,
    )

    completion = json.loads(
        collector.complete(ReviewCompletionSubmission(summary="Finish without retrying."))
    )

    assert completion == {
        "accepted": True,
        "comment_count": 0,
        "forced_completion": True,
        "incomplete_files": ["src/service.py"],
        "reviewed_files": [],
    }
    assert collector.incomplete_review_files == ("src/service.py",)


def test_review_file_completion_accepts_paths_supported_by_evidence_tools() -> None:
    long_path = f"src/{'nested/' * 40}service.py"

    submission = ReviewFileCompletionSubmission(reviewed_files=[long_path])

    assert list(submission.reviewed_files) == [long_path]


@pytest.mark.parametrize("retry_limit", [-1, 21])
async def test_comment_collector_rejects_invalid_completion_retry_limit(
    tmp_path: Path,
    retry_limit: int,
) -> None:
    snapshot = await _snapshot(tmp_path)

    with pytest.raises(ValueError, match="between 0 and 20"):
        ReviewCommentCollector(
            snapshot=snapshot,
            reviewer_id="correctness",
            confidence_floor=0.7,
            tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20),
            max_incomplete_review_retries=retry_limit,
        )


async def test_all_model_tools_work_through_agents_sdk_entrypoints(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    descriptions = {
        "find_files": "Find files with explicit path and pattern.",
        "grep": "Search visible text.",
        "read_file": "Read a versioned file range.",
        "get_diff": "Read a verified diff.",
        "comment": "Submit findings.",
        "review_file_done": "Record reviewed files.",
        "task_done": "Finish the review.",
    }
    custom_limits = ToolLimits(regex_timeout_seconds=30)
    filesystem_tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=30,
        tool_limits=custom_limits,
    )
    collector = ReviewCommentCollector(
        snapshot=snapshot,
        reviewer_id="correctness",
        confidence_floor=0.7,
        tools=filesystem_tools,
        tool_descriptions=descriptions,
    )
    agent_tools = {
        tool.name: tool
        for tool in (
            *filesystem_tools.as_agent_tools(descriptions),
            *collector.as_agent_tools(),
        )
    }
    read_file_agent_tool = agent_tools["read_file"]
    assert read_file_agent_tool.strict_json_schema is False
    assert read_file_agent_tool.params_json_schema["required"] == ["path", "version"]
    assert agent_tools["grep"].params_json_schema["required"] == [
        "pattern",
        "path",
        "file_pattern",
    ]

    async def invoke(name: str, arguments: dict[str, object]) -> dict[str, object]:
        serialized = json.dumps(arguments)
        result = await agent_tools[name].on_invoke_tool(
            ToolContext(
                None,
                usage=Usage(),
                tool_name=name,
                tool_call_id=f"sdk-{name}",
                tool_arguments=serialized,
                run_config=RunConfig(),
            ),
            serialized,
        )
        assert isinstance(result, str)
        assert not result.startswith("An error occurred")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        return parsed

    assert (await invoke("find_files", {"path": "src", "pattern": "*.py"}))["paths"] == [
        "src/helper.py",
        "src/service.py",
    ]
    assert len(
        (
            await invoke(
                "grep",
                {
                    "pattern": "return\\s+'new'",
                    "path": "src",
                    "file_pattern": "**/*.py",
                },
            )
        )["matches"]
    ) == 1
    whole_file = await invoke(
        "read_file",
        {"path": "src/service.py", "version": "current"},
    )
    assert "return 'new'" in str(whole_file["content"])
    for version, expected in (("current", "new"), ("base", "old"), ("head", "new")):
        read = await invoke(
            "read_file",
            {
                "path": "src/service.py",
                "start_line": 1,
                "end_line": 2,
                "version": version,
            },
        )
        assert expected in str(read["content"])
    assert "return 'new'" in str((await invoke("get_diff", {"path": "src/service.py"}))["content"])
    comment = await invoke(
        "comment",
        {
            "comments": [
                {
                    "path": "src/service.py",
                    "side": "new",
                    "existing_code": "return 'new'",
                    "title": "Changed return contract",
                    "content": "The changed return value needs confirmation.",
                    "recommendation": "Confirm the intended public contract.",
                    "category": "correctness",
                    "severity": "medium",
                    "confidence": 0.9,
                }
            ]
        },
    )
    assert comment["accepted_count"] == 1
    completion = await invoke(
        "review_file_done",
        {"reviewed_files": ["src/service.py"]},
    )
    assert completion["recorded_files"] == ["src/service.py"]
    completion = await invoke(
        "task_done",
        {"summary": "Reviewed the changed service file."},
    )
    assert completion["reviewed_files"] == ["src/service.py"]

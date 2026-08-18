import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.snapshot_tools import (
    FilesystemReviewTools,
    ModelLineRange,
)
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
from codelens.workspace.domain.review_file_scope import ReviewFileScope
from codelens.workspace.infrastructure.git_cli import GitCli


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _only_diff(result: str) -> dict[str, object]:
    payload = json.loads(result)
    assert payload["data"]["has_more"] is False
    assert payload["data"]["next_cursor"] is None
    assert len(payload["data"]["files"]) == 1
    file_result = payload["data"]["files"][0]
    return {
        **file_result,
        "content": file_result["header"] + "".join(file_result["hunks"]),
        "truncated": not file_result["is_complete"],
    }


def _tool_data(result: str) -> dict[str, object]:
    payload = json.loads(result)
    assert payload["schema_version"] == "2"
    assert isinstance(payload["data"], dict)
    return payload["data"]


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
            review_scope=ReviewFileScope.include_all(("src/service.py",), ("src/helper.py",)),
            instruction_paths=(
                "AGENTS.md",
                "REVIEW.md",
                "src/REVIEW.md",
                "src/service.py.review.md",
                "tests/REVIEW.md",
            ),
            entries=(
                SnapshotEntry(
                    "src/service.py", "file", 0o644, len(changed), _hash(changed), None, "target"
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


async def _multi_hunk_snapshot(tmp_path: Path) -> ReviewSnapshot:
    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "review@example.test")
    await _git(tmp_path, "config", "user.name", "Review Test")
    await _git(tmp_path, "config", "commit.gpgSign", "false")
    (tmp_path / "src").mkdir()
    base_lines = [f"line {line_number}\n" for line_number in range(1, 41)]
    path = "src/multi.py"
    (tmp_path / path).write_text("".join(base_lines))
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "base")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    current_lines = list(base_lines)
    for index in (1, 14, 27):
        current_lines[index] = f"changed {index + 1}\n"
    current = "".join(current_lines).encode()
    (tmp_path / path).write_bytes(current)
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "head")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    return ReviewSnapshot(
        "snapshot-multi",
        TaskWorktree("worktree-multi", "review-multi", "a" * 64, tmp_path, head_oid, "b" * 64),
        ReviewTarget(base_oid, head_oid, None),
        RepositoryFingerprint(head_oid, "c" * 64, "d" * 64),
        SnapshotManifest(
            ReviewFileScope.include_all((path,)),
            entries=(
                SnapshotEntry(path, "file", 0o100644, len(current), _hash(current), None, "target"),
            ),
        ),
        ChangeIndex(
            tuple(
                ChangedHunk(
                    f"hunk-{line_number}",
                    path,
                    line_number,
                    line_number,
                    "new",
                    _hash(f"changed {line_number}\n".encode()),
                )
                for line_number in (2, 15, 28)
            ),
            (ReviewFileChange(path, "modified"),),
        ),
    )


async def _contract_matrix_snapshot(tmp_path: Path) -> ReviewSnapshot:
    """Build the plan's shared real-Git matrix for all four evidence tools."""

    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "review@example.test")
    await _git(tmp_path, "config", "user.name", "Review Test")
    await _git(tmp_path, "config", "commit.gpgSign", "false")
    base_files = {
        "README.md": b"matrix fixture\n",
        "empty.txt": b"",
        "src/a.py": b"VALUE = 'old'\n",
        "src/deep/compiler_plan.py": b"needle = 'compiler'\n",
        "src/deep/blank_lines.py": b"first\n\n   \nlast\n",
        "tests/test_a.py": b"def test_a():\n    pass\n",
        "tests/unit/test_nested.py": b"def test_nested():\n    pass\n",
        "unicode/中文.py": "名称 = '值'\n".encode(),
        "long/single_line.txt": ("😀" * 200 + "\n").encode(),
        "binary/blob.bin": b"binary\0payload",
        "renamed/old_name.py": b"RENAMED = True\n",
        "deleted/gone.py": b"GONE = True\n",
    }
    for path, payload in base_files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    (tmp_path / "link-to-a").symlink_to("src/a.py")
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "matrix base")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()

    current_a = b"VALUE = 'new'\n"
    (tmp_path / "src/a.py").write_bytes(current_a)
    await _git(tmp_path, "mv", "renamed/old_name.py", "renamed/new_name.py")
    (tmp_path / "deleted/gone.py").unlink()
    (tmp_path / "link-to-a").unlink()
    (tmp_path / "link-to-a").symlink_to("README.md")
    await _git(tmp_path, "add", "-A")
    await _git(tmp_path, "commit", "-m", "matrix head")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()

    current_files = {
        **{
            path: payload
            for path, payload in base_files.items()
            if path not in {"src/a.py", "renamed/old_name.py", "deleted/gone.py"}
        },
        "src/a.py": current_a,
        "renamed/new_name.py": base_files["renamed/old_name.py"],
    }
    review_paths = (
        "src/a.py",
        "renamed/new_name.py",
        "deleted/gone.py",
        "link-to-a",
    )
    context_paths = tuple(sorted(path for path in current_files if path not in review_paths))
    entries = [
        SnapshotEntry(
            path,
            "file",
            0o100644,
            len(payload),
            _hash(payload),
            None,
            "target" if path in review_paths else "context",
        )
        for path, payload in sorted(current_files.items())
    ]
    entries.extend(
        (
            SnapshotEntry(
                "deleted/gone.py",
                "deleted",
                0,
                0,
                _hash(b""),
                None,
                "target",
            ),
            SnapshotEntry(
                "link-to-a",
                "symlink",
                0o120000,
                len(b"README.md"),
                _hash(b"README.md"),
                "README.md",
                "target",
            ),
        )
    )
    return ReviewSnapshot(
        "snapshot-contract-matrix",
        TaskWorktree(
            "worktree-contract-matrix",
            "review-contract-matrix",
            "a" * 64,
            tmp_path,
            head_oid,
            "b" * 64,
        ),
        ReviewTarget(base_oid, head_oid, None),
        RepositoryFingerprint(head_oid, "c" * 64, "d" * 64),
        SnapshotManifest(
            ReviewFileScope.include_all(review_paths, context_paths),
            entries=tuple(entries),
        ),
        ChangeIndex(
            (
                ChangedHunk(
                    "matrix-old-a",
                    "src/a.py",
                    1,
                    1,
                    "old",
                    _hash(base_files["src/a.py"]),
                ),
                ChangedHunk(
                    "matrix-new-a",
                    "src/a.py",
                    1,
                    1,
                    "new",
                    _hash(current_a),
                ),
            ),
            (
                ReviewFileChange("src/a.py", "modified"),
                ReviewFileChange(
                    "renamed/new_name.py",
                    "renamed",
                    old_path="renamed/old_name.py",
                ),
                ReviewFileChange("deleted/gone.py", "deleted"),
                ReviewFileChange("link-to-a", "modified"),
            ),
        ),
    )


async def test_all_evidence_tools_share_the_real_git_contract_matrix(
    tmp_path: Path,
) -> None:
    snapshot = await _contract_matrix_snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    found = _tool_data(await tools.find_files("", "*.py"))
    searched = _tool_data(await tools.grep("needle", "literal", "", "*.py"))
    blank_lines = _tool_data(await tools.read_file("src/deep/blank_lines.py", "current", None))
    diff = _tool_data(await tools.get_diff("", None))

    assert "src/deep/compiler_plan.py" in found["paths"]
    assert "tests/unit/test_nested.py" in found["paths"]
    assert "unicode/中文.py" in found["paths"]
    assert searched["matches"] == [
        {
            "path": "src/deep/compiler_plan.py",
            "line_number": 1,
            "line": "needle = 'compiler'",
        }
    ]
    assert blank_lines["content"] == ("   1 | first\n   2 | \n   3 |    \n   4 | last\n")
    assert {item["change_type"] for item in diff["files"]} == {
        "modified",
        "renamed",
        "deleted",
    }
    assert {item["path"] for item in diff["files"]} == set(snapshot.manifest.review_paths)
    assert tools.reviewed_paths == set(snapshot.manifest.review_paths)


async def test_exposes_only_hash_verified_snapshot_content(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    assert _tool_data(await tools.find_files(path="src"))["paths"] == [
        "src/helper.py",
        "src/service.py",
    ]
    assert _tool_data(await tools.find_files(pattern="src/*.py"))["paths"] == [
        "src/helper.py",
        "src/service.py",
    ]
    assert _tool_data(await tools.find_files(path="src", pattern="service.*"))["paths"] == [
        "src/service.py"
    ]
    assert _tool_data(await tools.grep("return"))["matches"] == [
        {"line_number": 2, "path": "src/helper.py", "line": "    return 'helper'"},
        {"line_number": 2, "path": "src/service.py", "line": "    return 'new'"},
    ]
    read = _tool_data(
        await tools.read_file("src/service.py", "current", ModelLineRange(start_line=1, end_line=2))
    )
    assert read["content"] == "   1 | def original() -> str:\n   2 |     return 'new'\n"
    assert read["version"] == "current"
    assert "content_hash" not in read

    (tmp_path / "src" / "helper.py").write_text("tampered\n")
    tampered = json.loads(
        await tools.read_file("src/helper.py", "current", ModelLineRange(start_line=1, end_line=1))
    )
    assert tampered["status"] == "rejected"
    invisible = json.loads(
        await tools.read_file(".git/config", "current", ModelLineRange(start_line=1, end_line=1))
    )
    assert invisible["status"] == "rejected"


async def test_find_and_grep_share_recursive_basename_glob_semantics(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    nested = b"def compiler_plan() -> str:\n    return 'nested'\n"
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "compiler_plan.py").write_bytes(nested)
    nested_path = "src/deep/compiler_plan.py"
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            review_scope=ReviewFileScope.include_all(
                snapshot.manifest.review_paths,
                (*snapshot.manifest.context_paths, nested_path),
            ),
            entries=(
                *snapshot.manifest.entries,
                SnapshotEntry(
                    nested_path,
                    "file",
                    0o100644,
                    len(nested),
                    _hash(nested),
                    None,
                    "context",
                ),
            ),
        ),
    )
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    found = _tool_data(await tools.find_files(path="src", pattern="*.py"))
    searched = _tool_data(await tools.grep("compiler_plan", path="src", file_pattern="*.py"))

    assert nested_path in found["paths"]
    assert searched["matches"] == [
        {"line_number": 1, "path": nested_path, "line": "def compiler_plan() -> str:"}
    ]


async def test_grep_reports_truncation_and_supports_narrower_retry(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    custom_limits = ToolLimits(max_scan_bytes=45)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20, tool_limits=custom_limits)

    broad_result = json.loads(await tools.grep("return", path="src"))
    assert broad_result["status"] == "partial"
    assert broad_result["data"]["matches"] == [
        {"line_number": 2, "path": "src/helper.py", "line": "    return 'helper'"}
    ]
    assert broad_result["data"]["truncated"] is True
    assert broad_result["diagnostics"][0]["code"] == "scan_limit_reached"

    narrower_result = _tool_data(await tools.grep("return", path="src", file_pattern="service.*"))
    assert narrower_result["matches"] == [
        {"line_number": 2, "path": "src/service.py", "line": "    return 'new'"}
    ]
    assert narrower_result["truncated"] is False

    outside_scope = json.loads(await tools.grep("return", path="tests"))
    assert outside_scope["status"] == "success"
    assert outside_scope["data"]["matches"] == []
    assert outside_scope["diagnostics"][0]["code"] == "no_candidate_files"


async def test_grep_filters_directory_scope_with_relative_file_pattern(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    result = _tool_data(
        await tools.grep(
            "return",
            path="src",
            file_pattern="service.*",
        )
    )

    assert result["matches"] == [
        {"line_number": 2, "path": "src/service.py", "line": "    return 'new'"}
    ]
    exact_file = _tool_data(
        await tools.grep(
            "return",
            path="src/service.py",
            file_pattern="*.py",
        )
    )
    assert exact_file["matches"] == result["matches"]
    exact_file_ignores_file_pattern = _tool_data(
        await tools.grep(
            "return",
            path="src/service.py",
            file_pattern="*.json",
        )
    )
    assert exact_file_ignores_file_pattern["matches"] == result["matches"]
    invalid_glob = json.loads(await tools.grep("return", path="src", file_pattern="../*.py"))
    assert invalid_glob["status"] == "rejected"
    assert invalid_glob["diagnostics"][0]["code"] == "invalid_glob_pattern"


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

    result = _tool_data(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).grep(
            "needle", path=path, file_pattern="*.py"
        )
    )

    assert len(result["matches"]) == 1
    assert "needle" in result["matches"][0]["line"]
    assert len(result["matches"][0]["line"]) <= 200


async def test_grep_distinguishes_literal_regex_and_no_content(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/helper.py"
    payload = b"a.b\naxb\n"
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
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    literal = _tool_data(await tools.grep("a.b", mode="literal", path=path))
    regex = _tool_data(await tools.grep("a.b", mode="regex", path=path))
    no_match = json.loads(await tools.grep("missing", mode="literal", path=path))
    invalid_regex = json.loads(await tools.grep("[", mode="regex", path=path))

    assert [match["line_number"] for match in literal["matches"]] == [1]
    assert [match["line_number"] for match in regex["matches"]] == [1, 2]
    assert no_match["status"] == "success"
    assert no_match["diagnostics"][0]["code"] == "no_content_matches"
    assert invalid_regex["status"] == "rejected"
    assert invalid_regex["diagnostics"][0]["code"] == "invalid_regular_expression"


async def test_grep_skips_binary_files_and_replaces_invalid_utf8_stably(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    binary_path = "src/blob.bin"
    invalid_utf8_path = "src/invalid-utf8.txt"
    binary_payload = b"needle\0binary\n"
    invalid_utf8_payload = b"prefix-\xff-needle\n"
    (tmp_path / binary_path).write_bytes(binary_payload)
    (tmp_path / invalid_utf8_path).write_bytes(invalid_utf8_payload)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                snapshot.manifest.review_paths,
                (
                    *snapshot.manifest.context_paths,
                    binary_path,
                    invalid_utf8_path,
                ),
            ),
            entries=(
                *snapshot.manifest.entries,
                SnapshotEntry(
                    binary_path,
                    "file",
                    0o100644,
                    len(binary_payload),
                    _hash(binary_payload),
                    None,
                    "context",
                ),
                SnapshotEntry(
                    invalid_utf8_path,
                    "file",
                    0o100644,
                    len(invalid_utf8_payload),
                    _hash(invalid_utf8_payload),
                    None,
                    "context",
                ),
            ),
        ),
    )

    result = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).grep(
            "needle",
            mode="literal",
            path="src",
            file_pattern="*",
        )
    )

    assert result["status"] == "success"
    assert result["data"]["candidate_file_count"] == 4
    assert result["data"]["scanned_file_count"] == 3
    assert result["data"]["skipped_binary_file_count"] == 1
    assert result["data"]["matches"] == [
        {
            "line_number": 1,
            "path": invalid_utf8_path,
            "line": "prefix-�-needle",
        }
    ]


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

    result = _tool_data(await tools.grep("match", path=path, file_pattern="*.py"))

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
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                snapshot.manifest.review_paths,
                (*snapshot.manifest.context_paths, nested_path),
            ),
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

    direct = _tool_data(await tools.find_files(path="src", pattern="*.py"))
    recursive = _tool_data(await tools.find_files(path="src", pattern="**/*.py"))

    assert direct["paths"] == [
        "src/helper.py",
        "src/nested/worker.py",
        "src/service.py",
    ]
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

    assert result["status"] == "partial"
    assert result["data"] == {
        "effective_pattern": "*.py",
        "matched_count": 2,
        "normalized_path": "src",
        "paths": ["src/helper.py"],
        "pattern_scope": "recursive_basename",
        "requested_path": "src",
        "requested_pattern": "*.py",
        "returned_count": 1,
        "scope_type": "directory",
        "truncated": True,
        "visible_file_count": 2,
    }
    assert result["diagnostics"][0]["code"] == "result_limit_reached"


async def test_find_files_distinguishes_empty_scope_no_match_and_file_path(
    tmp_path: Path,
) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=20)

    empty_scope = json.loads(await tools.find_files(path="missing", pattern="*.py"))
    no_match = json.loads(await tools.find_files(path="src", pattern="*.json"))
    file_scope = json.loads(await tools.find_files(path="src/service.py", pattern="*.py"))

    assert empty_scope["status"] == "success"
    assert empty_scope["data"]["visible_file_count"] == 0
    assert empty_scope["diagnostics"][0]["code"] == "empty_directory_scope"
    assert no_match["status"] == "success"
    assert no_match["data"]["visible_file_count"] == 2
    assert no_match["diagnostics"][0]["code"] == "no_files_match_pattern"
    assert file_scope["status"] == "rejected"
    assert file_scope["diagnostics"][0]["code"] == "path_is_not_directory"
    assert file_scope["diagnostics"][0]["suggested_arguments"] == {
        "path": "src",
        "pattern": "service.py",
    }


async def test_find_files_rejects_ambiguous_glob_with_complete_retry(
    tmp_path: Path,
) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=20)

    result = json.loads(await tools.find_files(path="src", pattern="**.py"))

    assert result["status"] == "rejected"
    assert result["diagnostics"][0]["code"] == "ambiguous_recursive_glob"
    assert result["diagnostics"][0]["suggested_arguments"] == {
        "path": "src",
        "pattern": "*.py",
    }


async def test_read_file_supports_bounded_whole_file_mode(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    read = json.loads(await tools.read_file("src/service.py"))

    assert read["status"] == "success"
    assert read["data"]["requested_line_range"] is None
    assert read["data"]["actual_line_range"] == {"start_line": 1, "end_line": 2}
    assert read["data"]["content"] == ("   1 | def original() -> str:\n   2 |     return 'new'\n")
    assert read["data"]["truncated"] is False


async def test_read_file_marks_whole_file_line_limit_as_truncated(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    custom_limits = ToolLimits(max_lines=1)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20, tool_limits=custom_limits)

    read = json.loads(await tools.read_file("src/service.py"))

    assert read["status"] == "partial"
    assert read["data"]["content"] == "   1 | def original() -> str:\n"
    assert read["data"]["actual_line_range"]["end_line"] == 1
    assert read["data"]["next_line_range"] == {"start_line": 2, "end_line": 2}


async def test_read_file_preserves_blank_whitespace_crlf_unicode_and_eof_clamp(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/helper.py"
    payload = "alpha\r\n\r\n   \r\n中文😀\nlast".encode()
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
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    result = json.loads(
        await tools.read_file(
            path,
            "current",
            ModelLineRange(start_line=1, end_line=99),
        )
    )
    beyond_eof = json.loads(
        await tools.read_file(
            path,
            "current",
            ModelLineRange(start_line=6, end_line=7),
        )
    )

    assert result["status"] == "success"
    assert result["data"]["total_lines"] == 5
    assert result["data"]["actual_line_range"] == {"start_line": 1, "end_line": 5}
    assert result["data"]["content"] == (
        "   1 | alpha\n   2 | \n   3 |    \n   4 | 中文😀\n   5 | last"
    )
    assert beyond_eof["status"] == "rejected"
    assert beyond_eof["diagnostics"][0]["code"] == "line_range_out_of_bounds"


async def test_read_file_marks_utf8_safe_incomplete_long_line_as_non_evidence(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/service.py"
    payload = ("😀" * 20).encode()
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
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=ToolLimits(max_read_bytes=17),
    )

    result = json.loads(await tools.read_file(path))

    assert result["status"] == "partial"
    assert result["data"]["line_content_truncated"] is True
    assert len(result["data"]["content"].encode("utf-8")) <= 17
    assert result["diagnostics"][0]["code"] == "line_exceeds_read_limit"
    assert tools.reviewed_paths == set()


async def test_read_file_agent_schema_requires_nullable_line_range(tmp_path: Path) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=20)
    descriptions = {name: name for name in ("find_files", "grep", "read_file", "get_diff")}

    read_tool = next(
        tool for tool in tools.as_agent_tools(descriptions) if tool.name == "read_file"
    )
    diff_tool = next(tool for tool in tools.as_agent_tools(descriptions) if tool.name == "get_diff")
    schema = read_tool.params_json_schema

    assert read_tool.strict_json_schema is False
    assert set(schema["required"]) == {"path"}
    assert "version" in schema["properties"]
    assert "start_line" in schema["properties"]
    assert "end_line" in schema["properties"]
    assert diff_tool.strict_json_schema is False
    assert set(diff_tool.params_json_schema["required"]) == {"path"}
    assert "cursor" in diff_tool.params_json_schema["properties"]


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

    result = json.loads(await tools.read_file(path))
    assert result["status"] == "rejected"
    assert result["diagnostics"][0]["code"] == "source_file_exceeds_limit"


async def test_provides_diff_and_bounded_base_version_reads(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)

    diff = _only_diff(await tools.get_diff("src/service.py"))
    assert "-    return 'old'" in diff["content"]
    assert "+    return 'new'" in diff["content"]
    assert "old mode" not in diff["content"]
    assert "new mode" not in diff["content"]
    assert "content_hash" not in diff
    line_range = ModelLineRange(start_line=1, end_line=2)
    revision = _tool_data(await tools.read_file("src/service.py", "base", line_range))
    base_content = revision["content"]
    assert "   2 |     return 'old'" in base_content
    assert "content_hash" not in revision
    head = _tool_data(await tools.read_file("src/service.py", "head", line_range))
    assert head["version"] == "head"
    assert "   2 |     return 'new'" in head["content"]
    invalid_version = json.loads(
        await tools.read_file("src/service.py", "arbitrary", line_range)  # type: ignore[arg-type]
    )
    assert invalid_version["status"] == "rejected"


async def test_get_diff_pages_directory_in_stable_path_order(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    added_entries: list[SnapshotEntry] = []
    added_files: list[ReviewFileChange] = []
    for path in ("src/zeta.py", "src/alpha.py"):
        payload = f"VALUE = {path!r}\n".encode()
        (tmp_path / path).write_bytes(payload)
        added_entries.append(
            SnapshotEntry(path, "file", 0o644, len(payload), _hash(payload), None, "target")
        )
        added_files.append(ReviewFileChange(path, "added"))
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                ("src/zeta.py", "src/service.py", "src/alpha.py"),
                snapshot.manifest.context_paths,
            ),
            entries=(*snapshot.manifest.entries, *added_entries),
        ),
        change_index=replace(
            snapshot.change_index,
            files=(*snapshot.change_index.files, *added_files),
        ),
    )
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=ToolLimits(max_results=2),
    )

    first = _tool_data(await tools.get_diff("src"))
    second = _tool_data(await tools.get_diff("src", cursor=first["next_cursor"]))

    assert [item["path"] for item in first["files"]] == [
        "src/alpha.py",
        "src/service.py",
    ]
    assert first["has_more"] is True
    assert isinstance(first["next_cursor"], str)
    assert [item["path"] for item in second["files"]] == ["src/zeta.py"]
    assert second["has_more"] is False
    assert second["next_cursor"] is None
    assert tools.reviewed_paths == {
        "src/alpha.py",
        "src/service.py",
        "src/zeta.py",
    }


async def test_get_diff_cursor_is_readable_and_rejects_malformed_values(
    tmp_path: Path,
) -> None:
    snapshot = await _multi_hunk_snapshot(tmp_path)
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=ToolLimits(max_read_bytes=300),
    )
    cursor = _tool_data(await tools.get_diff("src/multi.py"))["next_cursor"]
    assert isinstance(cursor, str)
    # Cursor is a readable "file_index:hunk_index" position token, not opaque base64.
    file_index, hunk_index = cursor.split(":")
    assert file_index.isdigit()
    assert hunk_index.isdigit()

    malformed = json.loads(await tools.get_diff("src/multi.py", cursor="not-a-valid-cursor"))
    assert malformed["status"] == "rejected"
    assert malformed["diagnostics"][0]["code"] == "invalid_diff_cursor"

    out_of_range = json.loads(await tools.get_diff("src/multi.py", cursor="999:0"))
    assert out_of_range["status"] == "rejected"
    assert out_of_range["diagnostics"][0]["code"] == "invalid_diff_cursor"


async def test_get_diff_pages_only_at_complete_hunk_boundaries(tmp_path: Path) -> None:
    snapshot = await _multi_hunk_snapshot(tmp_path)
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=ToolLimits(max_read_bytes=300),
    )

    first_raw = await tools.get_diff("src/multi.py")
    first = json.loads(first_raw)
    assert first["status"] == "partial"
    assert first["data"]["returned_hunk_count"] > 0
    assert first["data"]["files"][0]["is_complete"] is False
    assert first["data"]["completed_file_count"] == 0
    assert tools.reviewed_paths == set()
    cursor = first["data"]["next_cursor"]
    assert isinstance(cursor, str)
    assert await tools.get_diff("src/multi.py") == first_raw

    second = json.loads(await tools.get_diff("src/multi.py", cursor))

    all_hunks = [
        *first["data"]["files"][0]["hunks"],
        *second["data"]["files"][0]["hunks"],
    ]
    assert second["status"] == "success"
    assert len(all_hunks) == first["data"]["total_hunk_count"] == 3
    assert all(hunk.startswith("@@ ") for hunk in all_hunks)
    assert tools.reviewed_paths == {"src/multi.py"}


async def test_get_diff_cursor_skips_oversized_hunk_to_reach_later_hunks(
    tmp_path: Path,
) -> None:
    snapshot = await _multi_hunk_snapshot(tmp_path)
    path = "src/multi.py"
    oversized_content = (tmp_path / path).read_bytes().replace(
        b"changed 2\n",
        b"changed 2 " + (b"x" * 400) + b"\n",
    )
    (tmp_path / path).write_bytes(oversized_content)
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            entries=tuple(
                replace(
                    entry,
                    size_bytes=len(oversized_content),
                    content_hash=_hash(oversized_content),
                )
                if entry.path == path
                else entry
                for entry in snapshot.manifest.entries
            ),
        ),
    )
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=ToolLimits(max_read_bytes=300),
    )

    oversized_page = json.loads(await tools.get_diff(path))

    assert oversized_page["status"] == "needs_action"
    assert oversized_page["data"]["returned_hunk_count"] == 0
    assert oversized_page["diagnostics"][0]["code"] == "diff_hunk_exceeds_limit"
    assert oversized_page["data"]["read_file_suggestions"]
    cursor = oversized_page["data"]["next_cursor"]
    assert isinstance(cursor, str)

    remaining_page = json.loads(await tools.get_diff(path, cursor))

    assert remaining_page["status"] == "success"
    assert remaining_page["data"]["returned_hunk_count"] == 2
    assert remaining_page["data"]["has_more"] is False
    assert remaining_page["data"]["next_cursor"] is None


async def test_get_diff_omitting_cursor_starts_from_first_page(tmp_path: Path) -> None:
    snapshot = await _multi_hunk_snapshot(tmp_path)
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=ToolLimits(max_read_bytes=300),
    )

    # Omitting cursor (None) and passing no cursor both start from the first page.
    omitted = json.loads(await tools.get_diff("src/multi.py"))
    explicit_none = json.loads(await tools.get_diff("src/multi.py", cursor=None))
    assert omitted["data"]["returned_hunk_count"] == explicit_none["data"]["returned_hunk_count"]
    assert omitted["data"]["files"][0]["hunks"] == explicit_none["data"]["files"][0]["hunks"]


async def test_get_diff_distinguishes_empty_directory_and_non_review_file(
    tmp_path: Path,
) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=20)

    empty = json.loads(await tools.get_diff("tests"))
    context_file = json.loads(await tools.get_diff("src/helper.py"))

    assert empty["status"] == "success"
    assert empty["data"]["total_file_count"] == 0
    assert context_file["status"] == "rejected"
    assert context_file["diagnostics"][0]["code"] == "path_is_not_review_file"


async def test_successful_read_file_counts_as_review_coverage(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=ToolLimits(max_read_bytes=10),
    )

    await tools.read_file("src/service.py")
    result = json.loads(await tools.get_diff("src/service.py"))

    assert result["status"] == "needs_action"
    assert result["data"]["files"] == []
    assert result["diagnostics"][0]["code"] == "diff_hunk_exceeds_limit"
    assert result["data"]["read_file_suggestions"]
    assert tools.reviewed_paths == set()


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
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                (path,), snapshot.manifest.context_paths
            ),
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

    diff = _only_diff(
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

    diff = _only_diff(
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
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                (deleted_path,), snapshot.manifest.context_paths
            ),
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

    result = json.loads(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).read_file(
            deleted_path,
            "current",
            ModelLineRange(start_line=1, end_line=1),
        )
    )
    assert result["status"] == "rejected"


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

    result = json.loads(await tools.grep(r"(a+)+$", mode="regex"))

    assert result["status"] == "failed"
    assert result["diagnostics"][0]["code"] == "regular_expression_timed_out"


async def test_grep_timeout_starts_after_isolated_worker_is_ready(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    custom_limits = ToolLimits(regex_timeout_seconds=0.05)
    tools = FilesystemReviewTools(
        snapshot,
        GitCli(),
        max_tool_calls=20,
        tool_limits=custom_limits,
    )

    result = _tool_data(await tools.grep("return", mode="regex"))

    assert len(result["matches"]) == 2


async def test_get_diff_accepts_review_files_without_hunks(tmp_path: Path) -> None:
    snapshot = await _snapshot(tmp_path)
    snapshot = replace(snapshot, change_index=replace(snapshot.change_index, hunks=()))

    diff = _only_diff(
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
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                ("src/renamed.py",), snapshot.manifest.context_paths
            ),
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

    diff = _only_diff(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(
            "src/renamed.py"
        )
    )

    assert "rename from src/service.py" in diff["content"]
    assert "rename to src/renamed.py" in diff["content"]
    base = _tool_data(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).read_file(
            "src/renamed.py",
            "base",
            ModelLineRange(start_line=1, end_line=2),
        )
    )
    assert base["normalized_path"] == "src/renamed.py"
    assert base["version"] == "base"
    assert "   2 |     return 'new'" in base["content"]


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
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                (path,), snapshot.manifest.context_paths
            ),
            entries=(*snapshot.manifest.entries, entry),
        ),
        change_index=ChangeIndex(hunks=(), files=(ReviewFileChange(path, "modified"),)),
    )

    diff = _only_diff(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(path)
    )

    assert "Binary files" in diff["content"]


async def test_get_diff_accepts_mode_only_change_from_real_git_repository(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/executable.sh"
    payload = b"#!/bin/sh\nexit 0\n"
    file_path = tmp_path / path
    file_path.write_bytes(payload)
    file_path.chmod(0o644)
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "add script")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    file_path.chmod(0o755)
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "make script executable")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    snapshot = replace(
        snapshot,
        worktree=replace(snapshot.worktree, head_oid=head_oid),
        target=ReviewTarget(base_oid, head_oid, None),
        manifest=replace(
            snapshot.manifest,
            review_scope=ReviewFileScope.include_all((path,)),
            entries=(
                SnapshotEntry(
                    path,
                    "file",
                    0o755,
                    len(payload),
                    _hash(payload),
                    None,
                    "target",
                ),
            ),
        ),
        change_index=ChangeIndex((), (ReviewFileChange(path, "modified"),)),
    )

    tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20)
    diff = _only_diff(await tools.get_diff(path))

    assert "old mode 100644" in diff["content"]
    assert "new mode 100755" in diff["content"]
    assert tools.reviewed_paths == {path}


async def test_get_diff_accepts_symlink_target_change_from_real_git_repository(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "link-to-target"
    link_path = tmp_path / path
    link_path.symlink_to("target-a")
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "add symlink")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    link_path.unlink()
    link_path.symlink_to("target-b")
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "change symlink target")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    current_payload = b"target-b"
    snapshot = replace(
        snapshot,
        worktree=replace(snapshot.worktree, head_oid=head_oid),
        target=ReviewTarget(base_oid, head_oid, None),
        manifest=replace(
            snapshot.manifest,
            review_scope=ReviewFileScope.include_all((path,)),
            entries=(
                SnapshotEntry(
                    path,
                    "symlink",
                    0o120000,
                    len(current_payload),
                    _hash(current_payload),
                    "target-b",
                    "target",
                ),
            ),
        ),
        change_index=ChangeIndex((), (ReviewFileChange(path, "modified"),)),
    )

    diff = _only_diff(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(path)
    )["content"]

    assert "-target-a" in diff
    assert "+target-b" in diff


async def test_get_diff_accepts_deleted_file_from_real_git_repository(
    tmp_path: Path,
) -> None:
    snapshot = await _snapshot(tmp_path)
    path = "src/gone.py"
    base_payload = b"obsolete = True\n"
    (tmp_path / path).write_bytes(base_payload)
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "add obsolete file")
    base_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    (tmp_path / path).unlink()
    await _git(tmp_path, "add", "-A")
    await _git(tmp_path, "commit", "-m", "delete obsolete file")
    head_oid = (await GitCli().run(tmp_path, "rev-parse", "HEAD")).stdout.decode().strip()
    snapshot = replace(
        snapshot,
        worktree=replace(snapshot.worktree, head_oid=head_oid),
        target=ReviewTarget(base_oid, head_oid, None),
        manifest=replace(
            snapshot.manifest,
            review_scope=ReviewFileScope.include_all((path,)),
            entries=(
                SnapshotEntry(
                    path,
                    "deleted",
                    0,
                    0,
                    _hash(b""),
                    None,
                    "target",
                ),
            ),
        ),
        change_index=ChangeIndex((), (ReviewFileChange(path, "deleted"),)),
    )

    diff = _only_diff(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).get_diff(path)
    )["content"]

    assert "deleted file mode" in diff
    assert "-obsolete = True" in diff


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
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                (path,), snapshot.manifest.context_paths
            ),
            entries=(*snapshot.manifest.entries, entry),
        ),
        change_index=ChangeIndex(
            hunks=(ChangedHunk("new-hunk", path, 1, 2, "new", _hash(payload)),),
            files=(ReviewFileChange(path, "added"),),
        ),
    )

    diff = _only_diff(
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
            review_scope=snapshot.manifest.review_scope.with_visible_paths(
                (path,), snapshot.manifest.context_paths
            ),
            entries=(*snapshot.manifest.entries, entry),
        ),
        change_index=ChangeIndex(
            hunks=(ChangedHunk("link-hunk", path, 1, 1, "new", _hash(payload)),),
            files=(ReviewFileChange(path, "added"),),
        ),
    )

    result = _tool_data(
        await FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=20).read_file(
            path,
            "current",
            ModelLineRange(start_line=1, end_line=1),
        )
    )

    assert result["content"] == "   1 | service.py"


async def test_rejects_unbounded_tool_use(tmp_path: Path) -> None:
    tools = FilesystemReviewTools(await _snapshot(tmp_path), GitCli(), max_tool_calls=1)

    await tools.find_files(path="src")
    with pytest.raises(ValueError, match="budget"):
        await tools.find_files(pattern="**/*.py")

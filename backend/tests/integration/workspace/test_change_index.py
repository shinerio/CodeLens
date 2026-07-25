from pathlib import Path

from codelens.workspace.domain.models import ReviewFileChange, TaskWorktree
from codelens.workspace.infrastructure.change_index import GitChangeIndexBuilder
from codelens.workspace.infrastructure.git_cli import GitCli


async def _git(repository: Path, *args: str) -> str:
    result = await GitCli().run(repository, *args)
    assert result.returncode == 0
    return result.stdout.decode("utf-8", errors="strict").strip()


async def test_builds_typed_file_changes_and_all_new_ranges_from_real_git_diff(
    tmp_path: Path,
) -> None:
    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "review@example.test")
    await _git(tmp_path, "config", "user.name", "Review Test")
    await _git(tmp_path, "config", "commit.gpgSign", "false")
    (tmp_path / "deleted.py").write_text("removed = True\n", encoding="utf-8")
    (tmp_path / "modified.py").write_text(
        "".join(f"line_{line} = {line}\n" for line in range(1, 13)),
        encoding="utf-8",
    )
    (tmp_path / "original.py").write_text("renamed = True\n", encoding="utf-8")
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "base")
    base_oid = await _git(tmp_path, "rev-parse", "HEAD")

    (tmp_path / "added.py").write_text("added = True\n", encoding="utf-8")
    (tmp_path / "deleted.py").unlink()
    modified_lines = [f"line_{line} = {line}\n" for line in range(1, 13)]
    modified_lines[1] = "line_2 = 200\n"
    modified_lines[9] = "line_10 = 1000\n"
    (tmp_path / "modified.py").write_text("".join(modified_lines), encoding="utf-8")
    await _git(tmp_path, "mv", "original.py", "renamed.py")
    await _git(tmp_path, "add", "-A")
    await _git(tmp_path, "commit", "-m", "head")
    head_oid = await _git(tmp_path, "rev-parse", "HEAD")
    worktree = TaskWorktree(
        "worktree-1",
        "review-1",
        "a" * 64,
        tmp_path,
        head_oid,
        "b" * 64,
    )

    index = await GitChangeIndexBuilder(GitCli()).build(
        worktree,
        base_oid,
        ("renamed.py", "original.py", "modified.py", "deleted.py", "added.py"),
        "branch",
    )

    assert index.files == (
        ReviewFileChange("added.py", "added"),
        ReviewFileChange("deleted.py", "deleted"),
        ReviewFileChange("modified.py", "modified"),
        ReviewFileChange("renamed.py", "renamed", old_path="original.py"),
    )
    assert [(hunk.path, hunk.start_line, hunk.end_line, hunk.side) for hunk in index.hunks] == [
        ("added.py", 1, 1, "new"),
        ("deleted.py", 1, 1, "old"),
        ("modified.py", 2, 2, "new"),
        ("modified.py", 10, 10, "new"),
    ]


async def test_builds_hunks_for_git_c_quoted_utf8_paths(tmp_path: Path) -> None:
    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "review@example.test")
    await _git(tmp_path, "config", "user.name", "Review Test")
    await _git(tmp_path, "config", "commit.gpgSign", "false")
    path = "review-白皮书.md"
    (tmp_path / path).write_text("before\n", encoding="utf-8")
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "base")
    base_oid = await _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / path).write_text("after\n", encoding="utf-8")
    await _git(tmp_path, "add", path)
    await _git(tmp_path, "commit", "-m", "head")
    head_oid = await _git(tmp_path, "rev-parse", "HEAD")
    worktree = TaskWorktree(
        "worktree-quoted-path",
        "review-quoted-path",
        "a" * 64,
        tmp_path,
        head_oid,
        "b" * 64,
    )

    index = await GitChangeIndexBuilder(GitCli()).build(
        worktree,
        base_oid,
        (path,),
        "commit",
    )

    assert index.files == (ReviewFileChange(path, "modified"),)
    assert [(hunk.path, hunk.start_line, hunk.end_line, hunk.side) for hunk in index.hunks] == [
        (path, 1, 1, "new")
    ]


async def test_full_scope_indexes_complete_text_files_from_an_empty_baseline(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_bytes(b"line one\nline two\n")
    (tmp_path / "binary.dat").write_bytes(b"binary\0payload")
    (tmp_path / "empty.txt").write_bytes(b"")
    worktree = TaskWorktree(
        "worktree-full",
        "review-full",
        "a" * 64,
        tmp_path,
        "b" * 40,
        "c" * 64,
    )

    index = await GitChangeIndexBuilder(GitCli()).build(
        worktree,
        "b" * 40,
        ("src/service.py", "binary.dat", "empty.txt", "deleted.txt"),
        "full",
    )

    assert index.files == (
        ReviewFileChange("binary.dat", "added"),
        ReviewFileChange("deleted.txt", "deleted"),
        ReviewFileChange("empty.txt", "added"),
        ReviewFileChange("src/service.py", "added"),
    )
    assert [(hunk.path, hunk.start_line, hunk.end_line, hunk.side) for hunk in index.hunks] == [
        ("src/service.py", 1, 2, "new")
    ]

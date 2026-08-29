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
        ("modified.py", 2, 2, "old"),
        ("modified.py", 10, 10, "new"),
        ("modified.py", 10, 10, "old"),
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
        (path, 1, 1, "new"),
        (path, 1, 1, "old"),
    ]


async def test_skips_old_hunk_when_base_object_is_unreachable(tmp_path: Path) -> None:
    """When the old-side blob is unreachable (e.g. submodule), skip the old hunk gracefully."""
    git_cli = GitCli()

    submodule = tmp_path / "submodule"
    submodule.mkdir()
    await _git(submodule, "init")
    await _git(submodule, "config", "user.email", "review@example.test")
    await _git(submodule, "config", "user.name", "Review Test")
    await _git(submodule, "config", "commit.gpgSign", "false")
    (submodule / "lib.py").write_text("lib = 1\n", encoding="utf-8")
    await _git(submodule, "add", ".")
    await _git(submodule, "commit", "-m", "lib base")
    await _git(submodule, "commit", "-m", "lib head", "--allow-empty")

    origin = tmp_path / "origin"
    origin.mkdir()
    await _git(origin, "init")
    await _git(origin, "config", "user.email", "review@example.test")
    await _git(origin, "config", "user.name", "Review Test")
    await _git(origin, "config", "commit.gpgSign", "false")
    (origin / "main.py").write_text("main = 1\n", encoding="utf-8")
    await _git(origin, "add", ".")
    await _git(origin, "commit", "-m", "base")
    base_oid = await _git(origin, "rev-parse", "HEAD")

    await _git(
        origin,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(submodule),
        ".codehub/sub",
    )
    await _git(origin, "commit", "-m", "add submodule")
    (origin / "main.py").write_text("main = 2\n", encoding="utf-8")
    await _git(origin, "add", ".")
    await _git(origin, "commit", "-m", "head")
    head_oid = await _git(origin, "rev-parse", "HEAD")

    worktree = TaskWorktree(
        "worktree-submodule",
        "review-submodule",
        "a" * 64,
        origin,
        head_oid,
        "b" * 64,
    )

    index = await GitChangeIndexBuilder(git_cli).build(
        worktree,
        base_oid,
        ("main.py", ".codehub/sub"),
        "branch",
    )

    assert index.files == (
        ReviewFileChange(".codehub/sub", "added"),
        ReviewFileChange("main.py", "modified"),
    )
    assert all(hunk.path == "main.py" or hunk.side == "new" for hunk in index.hunks)


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


async def test_whitespace_only_modified_files_are_excluded(tmp_path: Path) -> None:
    """Files whose changes are purely whitespace must not appear in the ChangeIndex."""

    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "review@example.test")
    await _git(tmp_path, "config", "user.name", "Review Test")
    await _git(tmp_path, "config", "commit.gpgSign", "false")
    (tmp_path / "real_change.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "indent_only.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "crlf_only.py").write_text("c = 3\n", encoding="utf-8")
    (tmp_path / "blank_lines.py").write_text("d = 4\n", encoding="utf-8")
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "base")
    base_oid = await _git(tmp_path, "rev-parse", "HEAD")

    # real_change.py: substantive code change
    (tmp_path / "real_change.py").write_text("x = 100\n", encoding="utf-8")
    # indent_only.py: only indentation changed
    (tmp_path / "indent_only.py").write_text("    a = 1\n    b = 2\n", encoding="utf-8")
    # crlf_only.py: only \n -> \r\n changed
    (tmp_path / "crlf_only.py").write_bytes(b"c = 3\r\n")
    # blank_lines.py: only extra blank lines added
    (tmp_path / "blank_lines.py").write_text("d = 4\n\n\n", encoding="utf-8")
    await _git(tmp_path, "add", "-A")
    await _git(tmp_path, "commit", "-m", "head")
    head_oid = await _git(tmp_path, "rev-parse", "HEAD")
    worktree = TaskWorktree(
        "worktree-ws",
        "review-ws",
        "a" * 64,
        tmp_path,
        head_oid,
        "b" * 64,
    )

    index = await GitChangeIndexBuilder(GitCli()).build(
        worktree,
        base_oid,
        ("real_change.py", "indent_only.py", "crlf_only.py", "blank_lines.py"),
        "branch",
    )

    # Only the substantive change should appear; whitespace-only files are excluded.
    assert index.files == (
        ReviewFileChange("real_change.py", "modified"),
    )
    assert all(hunk.path == "real_change.py" for hunk in index.hunks)
    # Whitespace-only files are recorded so downstream consumers can
    # distinguish a deliberate filter from a genuine metadata gap.
    assert index.whitespace_only_paths == (
        "blank_lines.py",
        "crlf_only.py",
        "indent_only.py",
    )


async def test_substantive_change_alongside_whitespace_is_kept(tmp_path: Path) -> None:
    """A file with both whitespace and substantive changes must be kept."""

    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "review@example.test")
    await _git(tmp_path, "config", "user.name", "Review Test")
    await _git(tmp_path, "config", "commit.gpgSign", "false")
    (tmp_path / "mixed.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "commit", "-m", "base")
    base_oid = await _git(tmp_path, "rev-parse", "HEAD")

    # Line 1: whitespace-only change (indentation)
    # Line 2: substantive change (value)
    (tmp_path / "mixed.py").write_text("    a = 1\nb = 200\nc = 3\n", encoding="utf-8")
    await _git(tmp_path, "add", "-A")
    await _git(tmp_path, "commit", "-m", "head")
    head_oid = await _git(tmp_path, "rev-parse", "HEAD")
    worktree = TaskWorktree(
        "worktree-mixed",
        "review-mixed",
        "a" * 64,
        tmp_path,
        head_oid,
        "b" * 64,
    )

    index = await GitChangeIndexBuilder(GitCli()).build(
        worktree,
        base_oid,
        ("mixed.py",),
        "branch",
    )

    # File must be kept because it has a substantive change.
    assert index.files == (ReviewFileChange("mixed.py", "modified"),)
    assert len(index.hunks) > 0

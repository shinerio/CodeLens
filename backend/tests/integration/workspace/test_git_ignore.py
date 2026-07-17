from pathlib import Path

from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_ignore import GitIgnoreResolver


async def test_excludes_tracked_file_matching_current_gitignore(git_repository: Path) -> None:
    tracked = git_repository / "tracked.log"
    tracked.write_text("old log\n", encoding="utf-8")
    git = GitCli()
    await git.run(git_repository, "add", "tracked.log")
    await git.run(git_repository, "commit", "-m", "track log")
    (git_repository / ".gitignore").write_text("*.log\n!important.log\n", encoding="utf-8")
    (git_repository / "important.log").write_text("keep\n", encoding="utf-8")

    result = await GitIgnoreResolver(git).resolve(
        git_repository,
        ("tracked.log", "important.log", "README.md"),
    )

    assert result.included == ("README.md", "important.log")
    assert result.excluded[0].path == "tracked.log"
    assert result.excluded[0].source == ".gitignore:1:*.log"


async def test_honors_nested_gitignore(git_repository: Path) -> None:
    generated = git_repository / "src" / "generated"
    generated.mkdir(parents=True)
    (git_repository / "src" / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (generated / "api.py").write_text("value = 1\n", encoding="utf-8")

    result = await GitIgnoreResolver(GitCli()).resolve(
        git_repository,
        ("src/generated/api.py",),
    )

    assert result.included == ()
    assert result.excluded[0].source is not None
    assert result.excluded[0].source.startswith("src/.gitignore:1:")

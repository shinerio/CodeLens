from pathlib import Path

import pytest

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.application.inspect_repository import RepositoryInspector
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter


async def test_inspects_repository(git_repository: Path) -> None:
    inspector = RepositoryInspector(
        GitRepositoryMetadataAdapter(GitCli()),
        repository_roots=(git_repository.parent,),
    )

    info = await inspector.inspect(git_repository)

    assert info.path == git_repository
    assert info.current_branch == "main"
    assert len(info.head_sha) == 40
    assert not info.is_dirty


async def test_rejects_path_outside_repository_roots(git_repository: Path) -> None:
    inspector = RepositoryInspector(
        GitRepositoryMetadataAdapter(GitCli()),
        repository_roots=(git_repository / "nested",),
    )

    with pytest.raises(InvalidRepositoryError, match="outside configured repository roots"):
        await inspector.inspect(git_repository)


async def test_git_cli_rejects_output_over_limit(git_repository: Path) -> None:
    large_file = git_repository / "large.txt"
    large_file.write_text("x" * 128, encoding="utf-8")
    setup_git = GitCli()
    await setup_git.run(git_repository, "add", "large.txt")
    await setup_git.run(git_repository, "commit", "-m", "add large fixture")

    with pytest.raises(InvalidRepositoryError, match="output limit"):
        await GitCli(max_output_bytes=32).run(git_repository, "show", "HEAD:large.txt")

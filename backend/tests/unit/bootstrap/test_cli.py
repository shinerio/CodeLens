import asyncio
from pathlib import Path

import pytest

from codelens.bootstrap.cli import _parse_start_args, prepare_runtime
from codelens.bootstrap.settings import Settings
from codelens.workspace.infrastructure.git_cli import GitCli


class RecordingGitCli(GitCli):
    def __init__(self) -> None:
        super().__init__()
        self.verified_repository: Path | None = None

    async def verify_available(self, repository: Path) -> None:
        self.verified_repository = repository


def test_start_command_uses_default_lan_host_and_data_directory_option(tmp_path: Path) -> None:
    parsed = _parse_start_args(["start", "--data-dir", str(tmp_path / "data")])

    assert parsed.settings.host == "0.0.0.0"
    assert parsed.settings.repository_roots == ()


async def test_prepare_runtime_rejects_a_non_directory_data_path(tmp_path: Path) -> None:
    data_path = tmp_path / "not-a-directory"
    await asyncio.to_thread(data_path.write_text, "file")

    with pytest.raises(ValueError, match="data directory"):
        await prepare_runtime(Settings(data_dir=data_path))


async def test_prepare_runtime_verifies_git_before_startup(tmp_path: Path) -> None:
    git = RecordingGitCli()

    await prepare_runtime(Settings(data_dir=tmp_path / "data"), git=git)

    assert git.verified_repository == Path.cwd()

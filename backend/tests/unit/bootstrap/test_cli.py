import asyncio
from pathlib import Path

import pytest

from codelens.bootstrap.cli import parse_command, prepare_runtime
from codelens.bootstrap.settings import Settings


def test_start_command_uses_validated_loopback_and_data_directory_options(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    parsed = parse_command(
        ["start", str(repository), "--data-dir", str(tmp_path / "data")]
    )

    assert parsed.settings.host == "127.0.0.1"
    assert parsed.settings.repository_roots == (repository.resolve(),)


async def test_prepare_runtime_rejects_a_non_directory_data_path(tmp_path: Path) -> None:
    data_path = tmp_path / "not-a-directory"
    await asyncio.to_thread(data_path.write_text, "file")

    with pytest.raises(ValueError, match="data directory"):
        await prepare_runtime(Settings(data_dir=data_path))

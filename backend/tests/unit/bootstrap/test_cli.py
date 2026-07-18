import asyncio
from pathlib import Path

import pytest

from codelens.bootstrap.cli import parse_command, prepare_runtime, supervise
from codelens.bootstrap.settings import Settings
from codelens.worker.main import build_worker


class FakeChild:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def exit(self, returncode: int) -> None:
        self.returncode = returncode
        self._exited.set()

    def terminate(self) -> None:
        self.terminated = True
        self.exit(-15)

    def kill(self) -> None:
        self.killed = True
        self.exit(-9)


@pytest.mark.parametrize("command", ("api", "worker", "start"))
def test_commands_share_validated_loopback_and_data_directory_options(
    tmp_path: Path,
    command: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    parsed = parse_command(
        [command, str(repository), "--data-dir", str(tmp_path / "data")]
    )

    assert parsed.name == command
    assert parsed.settings.host == "127.0.0.1"
    assert parsed.settings.repository_roots == (repository.resolve(),)


async def test_prepare_runtime_rejects_a_non_directory_data_path(tmp_path: Path) -> None:
    data_path = tmp_path / "not-a-directory"
    await asyncio.to_thread(data_path.write_text, "file")

    with pytest.raises(ValueError, match="data directory"):
        await prepare_runtime(Settings(data_dir=data_path))


async def test_supervisor_starts_exactly_api_and_worker_and_stops_peer_on_failure(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    children = [FakeChild(101), FakeChild(102)]
    invocations: list[tuple[str, ...]] = []

    async def spawn(arguments: tuple[str, ...]) -> FakeChild:
        invocations.append(arguments)
        child = children[len(invocations) - 1]
        if "worker" in arguments:
            asyncio.get_running_loop().call_soon(child.exit, 7)
        return child

    result = await supervise(
        settings,
        spawn=spawn,
        grace_seconds=0.01,
        stop_event=asyncio.Event(),
    )

    invoked_commands = [
        [name for name in ("api", "worker", "start") if name in call]
        for call in invocations
    ]
    assert invoked_commands == [
        ["api"],
        ["worker"],
    ]
    assert children[0].terminated
    assert result != 0


def test_worker_composition_does_not_require_model_configuration(tmp_path: Path) -> None:
    components = build_worker(Settings(data_dir=tmp_path / "data"))

    assert components.settings.data_dir == tmp_path / "data"

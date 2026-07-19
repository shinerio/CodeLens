import argparse
import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "1")
os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "1")

import uvicorn

from codelens.bootstrap.logging import configure_process_logging
from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app

type CommandName = Literal["api", "worker", "start"]


@dataclass(frozen=True)
class ParsedCommand:
    """Carry one validated process command and its shared runtime settings."""

    name: CommandName
    settings: Settings


class ChildProcessPort(Protocol):
    """Expose the bounded process operations used by the local supervisor."""

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


@dataclass
class _ProcessGroupChild:
    process: asyncio.subprocess.Process

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def wait(self) -> int:
        return await self.process.wait()

    def terminate(self) -> None:
        if sys.platform == "win32":
            self.process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
            return
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def kill(self) -> None:
        if sys.platform == "win32":
            self.process.kill()
            return
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


type ChildSpawner = Callable[[tuple[str, ...]], Awaitable[ChildProcessPort]]


def _parser() -> argparse.ArgumentParser:
    defaults = Settings()
    parser = argparse.ArgumentParser(prog="codelens-review")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("api", "worker", "start"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("repository_root", nargs="*")
        subparser.add_argument("--host", default=defaults.host)
        subparser.add_argument("--port", type=int, default=defaults.port)
        subparser.add_argument("--data-dir", type=Path, default=defaults.data_dir)
    return parser


def parse_command(arguments: Sequence[str]) -> ParsedCommand:
    """Parse all process modes through one loopback and one-Worker Settings boundary."""

    values = _parser().parse_args(arguments)
    command = str(values.command)
    if command not in {"api", "worker", "start"}:
        raise ValueError("unsupported process command")
    settings = Settings(
        data_dir=Path(values.data_dir),
        host=str(values.host),
        port=int(values.port),
        repository_roots=tuple(Path(value) for value in values.repository_root),
    )
    return ParsedCommand(name=cast(CommandName, command), settings=settings)


async def prepare_runtime(settings: Settings) -> None:
    """Validate and create the contained application data directory before startup."""

    data_dir = settings.data_dir.expanduser().resolve()
    try:
        await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
    except FileExistsError:
        raise ValueError("configured data directory is not a directory") from None
    if not await asyncio.to_thread(data_dir.is_dir):
        raise ValueError("configured data directory is not a directory")


def run_api(settings: Settings) -> None:
    """Run only the loopback HTTP API process."""

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


def _child_arguments(command: Literal["api", "worker"], settings: Settings) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "codelens.bootstrap.cli",
        command,
        *(str(root) for root in settings.repository_roots),
        "--host",
        settings.host,
        "--port",
        str(settings.port),
        "--data-dir",
        str(settings.data_dir),
    )


async def _spawn_child(arguments: tuple[str, ...]) -> ChildProcessPort:
    if sys.platform == "win32":
        process = await asyncio.create_subprocess_exec(
            *arguments,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    else:
        process = await asyncio.create_subprocess_exec(*arguments, start_new_session=True)
    return _ProcessGroupChild(process)


async def _stop_children(
    children: tuple[ChildProcessPort, ...],
    *,
    grace_seconds: float,
) -> None:
    pending = tuple(child for child in children if child.returncode is None)
    for child in pending:
        child.terminate()
    if not pending:
        return
    try:
        async with asyncio.timeout(grace_seconds):
            await asyncio.gather(*(child.wait() for child in pending))
    except TimeoutError:
        for child in pending:
            if child.returncode is None:
                child.kill()
        await asyncio.gather(*(child.wait() for child in pending))


def _install_stop_handlers(stop_event: asyncio.Event) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signum)

    def remove() -> None:
        for signum in installed:
            loop.remove_signal_handler(signum)

    return remove


async def supervise(
    settings: Settings,
    *,
    spawn: ChildSpawner | None = None,
    grace_seconds: float = 5.0,
    stop_event: asyncio.Event | None = None,
) -> int:
    """Supervise exactly one API and Worker process with bounded group termination."""

    if grace_seconds <= 0:
        raise ValueError("supervisor grace period must be positive")
    await prepare_runtime(settings)
    spawn_child = spawn or _spawn_child
    requested_stop = stop_event or asyncio.Event()
    remove_handlers = _install_stop_handlers(requested_stop)
    children: list[ChildProcessPort] = []
    waiters: set[asyncio.Task[int | bool]] = set()
    try:
        for command in ("api", "worker"):
            children.append(await spawn_child(_child_arguments(command, settings)))
        child_waiters = {
            asyncio.create_task(child.wait()): child for child in children
        }
        stop_waiter = asyncio.create_task(requested_stop.wait())
        waiters = {*child_waiters, stop_waiter}
        done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if stop_waiter in done and requested_stop.is_set():
            exit_code = 0
        else:
            completed = next(task for task in done if task in child_waiters)
            child_code = int(completed.result())
            exit_code = child_code if child_code != 0 else 1
        await _stop_children(tuple(children), grace_seconds=grace_seconds)
        return exit_code
    finally:
        remove_handlers()
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        if children:
            await _stop_children(tuple(children), grace_seconds=grace_seconds)


def main(arguments: Sequence[str] | None = None) -> None:
    """Dispatch independent API/Worker modes or the bounded local supervisor."""

    command = parse_command(sys.argv[1:] if arguments is None else arguments)
    asyncio.run(prepare_runtime(command.settings))
    if command.name == "api":
        configure_process_logging("api", data_directory=command.settings.data_dir)
        run_api(command.settings)
        return
    if command.name == "worker":
        configure_process_logging("worker", data_directory=command.settings.data_dir)
        from codelens.worker.main import run_worker
        from codelens.worker.singleton import WorkerAlreadyRunningError

        try:
            asyncio.run(run_worker(command.settings))
        except WorkerAlreadyRunningError as error:
            print(error.code, file=sys.stderr)
            raise SystemExit(2) from None
        return
    configure_process_logging("supervisor", data_directory=command.settings.data_dir)
    exit_code = asyncio.run(supervise(command.settings))
    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "1")
os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "1")

from codelens.bootstrap.settings import Settings
from codelens.bootstrap.supervisor import Supervisor, SupervisorConfig
from codelens.workspace.infrastructure.git_cli import GitCli


@dataclass(frozen=True)
class ParsedStartCommand:
    """Carry validated start arguments: backend settings and frontend config."""

    settings: Settings
    supervisor_config: SupervisorConfig


def _add_start_flags(parser: argparse.ArgumentParser, defaults: Settings) -> None:
    """Register shared start/restart CLI flags on a subparser."""

    parser.add_argument("repository_root", nargs="*")
    parser.add_argument("--host", default=defaults.host, help="Host for both frontend and backend")
    parser.add_argument("--port", type=int, default=defaults.port, help="Backend port")
    parser.add_argument("--backend-host", default=None, help="Override backend host")
    parser.add_argument("--frontend-host", default=None, help="Override frontend host")
    parser.add_argument("--backend-port", type=int, default=None, help="Override backend port")
    parser.add_argument("--frontend-port", type=int, default=None, help="Override frontend port")
    parser.add_argument("--data-dir", type=Path, default=defaults.data_dir)


def _parser() -> argparse.ArgumentParser:
    defaults = Settings()
    parser = argparse.ArgumentParser(prog="codelens-review")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start backend and frontend services")
    _add_start_flags(start, defaults)

    subparsers.add_parser("stop", help="Stop all running services")

    restart = subparsers.add_parser("restart", help="Restart all services")
    _add_start_flags(restart, defaults)

    return parser


def _parse_start_args(arguments: Sequence[str]) -> ParsedStartCommand:
    """Parse and resolve start/restart arguments into backend settings and frontend config."""

    values = _parser().parse_args(arguments)

    # Resolve hosts: --host applies to both, individual flags override
    backend_host = values.backend_host if values.backend_host is not None else values.host
    frontend_host = values.frontend_host if values.frontend_host is not None else values.host
    backend_port = values.backend_port if values.backend_port is not None else values.port
    frontend_port = values.frontend_port if values.frontend_port is not None else 5173

    settings = Settings(
        data_dir=Path(values.data_dir),
        host=str(backend_host),
        port=int(backend_port),
        repository_roots=tuple(Path(value) for value in values.repository_root),
    )
    supervisor_config = SupervisorConfig(
        frontend_host=str(frontend_host),
        frontend_port=int(frontend_port),
    )
    return ParsedStartCommand(settings=settings, supervisor_config=supervisor_config)


async def prepare_runtime(settings: Settings, *, git: GitCli | None = None) -> None:
    """Validate external prerequisites and create the contained data directory."""

    data_dir = settings.data_dir.expanduser().resolve()
    try:
        await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
    except FileExistsError:
        raise ValueError("configured data directory is not a directory") from None
    if not await asyncio.to_thread(data_dir.is_dir):
        raise ValueError("configured data directory is not a directory")
    await (git or GitCli()).verify_available(Path.cwd())


def main(arguments: Sequence[str] | None = None) -> None:
    """Dispatch start/stop/restart commands to the supervisor."""

    argv = sys.argv[1:] if arguments is None else list(arguments)
    parser = _parser()
    parsed = parser.parse_args(argv)
    command = parsed.command

    supervisor = Supervisor()

    if command == "stop":
        supervisor.stop()
        return

    if command == "restart":
        start_cmd = _parse_start_args(argv)
        supervisor.restart(start_cmd.settings, start_cmd.supervisor_config)
        return

    # start
    start_cmd = _parse_start_args(argv)
    asyncio.run(prepare_runtime(start_cmd.settings))
    supervisor.start(start_cmd.settings, start_cmd.supervisor_config)


if __name__ == "__main__":
    main()

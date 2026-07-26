import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "1")
os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "1")

from codelens.bootstrap.logging import configure_process_logging
from codelens.bootstrap.settings import Settings
from codelens.workspace.infrastructure.git_cli import GitCli


@dataclass(frozen=True)
class ParsedCommand:
    """Carry one validated process command and its shared runtime settings."""

    settings: Settings


def _parser() -> argparse.ArgumentParser:
    defaults = Settings()
    parser = argparse.ArgumentParser(prog="codelens-review")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("repository_root", nargs="*")
    start.add_argument("--host", default=defaults.host)
    start.add_argument("--port", type=int, default=defaults.port)
    start.add_argument("--data-dir", type=Path, default=defaults.data_dir)
    return parser


def parse_command(arguments: Sequence[str]) -> ParsedCommand:
    """Parse the unified backend process options through one Settings boundary."""

    values = _parser().parse_args(arguments)
    settings = Settings(
        data_dir=Path(values.data_dir),
        host=str(values.host),
        port=int(values.port),
        repository_roots=tuple(Path(value) for value in values.repository_root),
    )
    return ParsedCommand(settings=settings)


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
    """Run the unified API and Worker backend process."""

    command = parse_command(sys.argv[1:] if arguments is None else arguments)
    asyncio.run(prepare_runtime(command.settings))
    from codelens.bootstrap.unified import run_unified

    configure_process_logging("unified", data_directory=command.settings.data_dir)
    asyncio.run(run_unified(command.settings))


if __name__ == "__main__":
    main()

import argparse

import uvicorn

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def main() -> None:
    """Run the CodeLens process entry point with validated local-only defaults."""

    parser = argparse.ArgumentParser(prog="codelens-review")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("repository_root", nargs="*")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()

    settings = Settings(
        host=arguments.host,
        port=arguments.port,
        repository_roots=tuple(arguments.repository_root),
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)

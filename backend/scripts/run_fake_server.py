from __future__ import annotations

import argparse
import asyncio
import subprocess
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import (
    LocalHttpSafetyMiddleware,
    _domain_problem,
)
from codelens.interface.http.dependencies import HttpProblem, build_components
from codelens.interface.http.routers.repositories import router as repositories_router
from codelens.interface.http.routers.reviews import router as reviews_router
from codelens.shared.domain.errors import DomainError
from codelens.testing.correctness_fixture import (
    FixtureRuntime,
    load_simple_branch_batch,
    prepare_simple_branch_repository,
)
from codelens.worker.main import build_worker


def _parser() -> argparse.ArgumentParser:
    defaults = Settings()
    parser = argparse.ArgumentParser(prog="run_fake_server")
    parser.add_argument(
        "--repository-root",
        action="append",
        type=Path,
        default=[],
        dest="repository_roots",
    )
    parser.add_argument("--data-dir", type=Path, default=defaults.data_dir)
    return parser


async def _build_app(settings: Settings) -> FastAPI:
    if settings.repository_roots:
        if len(settings.repository_roots) != 1:
            raise ValueError("fake server expects exactly one repository root")
        repository = settings.repository_roots[0]
        head_oid = (
            await asyncio.to_thread(
                lambda: subprocess.run(
                    ["git", "-C", str(repository), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                ).stdout.strip()
            )
        )
        batch = await load_simple_branch_batch(repository, base_oid=head_oid)
    else:
        fixture = await prepare_simple_branch_repository(settings.data_dir / "e2e-fixture")
        repository = fixture.repository
        batch = fixture.batch
        settings = settings.model_copy(update={"repository_roots": (repository,)})
    components = build_components(settings)
    worker = build_worker(settings, runtime=FixtureRuntime(batch))
    stop_event = asyncio.Event()
    worker_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal worker_task
        await components.start()
        worker_task = asyncio.create_task(worker.run(stop_event))
        try:
            yield
        finally:
            stop_event.set()
            if worker_task is not None:
                await worker_task
            await components.close()

    app = FastAPI(title="CodeLens Review API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.components = components
    app.add_middleware(LocalHttpSafetyMiddleware, configured_host="127.0.0.1")

    @app.exception_handler(DomainError)
    async def _handle_domain_error(_request: Request, error: DomainError) -> JSONResponse:
        status_code, message = _domain_problem(error)
        return JSONResponse(
            {"code": error.code, "message": message},
            status_code=status_code,
        )

    @app.exception_handler(HttpProblem)
    async def _handle_http_problem(_request: Request, error: HttpProblem) -> JSONResponse:
        return JSONResponse(
            {"code": error.code, "message": error.message},
            status_code=error.status_code,
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ready", "auth": settings.auth}

    app.include_router(repositories_router)
    app.include_router(reviews_router)
    return app


def main(arguments: Sequence[str] | None = None) -> None:
    values = _parser().parse_args(arguments)
    settings = Settings(
        data_dir=Path(values.data_dir),
        repository_roots=tuple(Path(value) for value in values.repository_roots),
    )
    app = asyncio.run(_build_app(settings))
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()

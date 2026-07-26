from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from codelens.bootstrap.settings import Settings
from codelens.bootstrap.unified import build_unified_backend
from codelens.interface.http.app import (
    LocalHttpSafetyMiddleware,
    _domain_problem,
)
from codelens.interface.http.dependencies import HttpProblem
from codelens.interface.http.routers.repositories import router as repositories_router
from codelens.interface.http.routers.reviewer_prompts import router as reviewer_prompts_router
from codelens.interface.http.routers.reviews import router as reviews_router
from codelens.interface.http.routers.settings import router as settings_router
from codelens.shared.domain.errors import DomainError
from codelens.testing.correctness_fixture import (
    FixtureRuntime,
    load_simple_branch_comments,
    prepare_simple_branch_repository,
)


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
    parser.add_argument("--port", type=int, default=8800)
    return parser


async def _build_app(settings: Settings) -> FastAPI:
    if settings.repository_roots:
        if len(settings.repository_roots) != 1:
            raise ValueError("fake server expects exactly one repository root")
        repository = settings.repository_roots[0]
    else:
        fixture = await prepare_simple_branch_repository(settings.data_dir / "e2e-fixture")
        repository = fixture.repository
        settings = settings.model_copy(update={"repository_roots": (repository,)})
    backend = build_unified_backend(
        settings,
        runtime=FixtureRuntime(
            load_simple_branch_comments(),
            repeat_first_comment=True,
        ),
    )
    components = backend.components
    stop_event = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal scheduler_task
        await backend.start()
        scheduler_task = asyncio.create_task(backend.scheduler.run(stop_event))
        try:
            yield
        finally:
            stop_event.set()
            if scheduler_task is not None:
                await scheduler_task
            await backend.close()

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
    app.include_router(settings_router)
    app.include_router(reviewer_prompts_router)
    return app


def main(arguments: Sequence[str] | None = None) -> None:
    values = _parser().parse_args(arguments)
    settings = Settings(
        data_dir=Path(values.data_dir),
        repository_roots=tuple(Path(value) for value in values.repository_roots),
    )
    app = asyncio.run(_build_app(settings))
    uvicorn.run(app, host="127.0.0.1", port=values.port)


if __name__ == "__main__":
    main()

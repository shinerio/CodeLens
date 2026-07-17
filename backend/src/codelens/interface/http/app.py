from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from codelens.bootstrap.settings import Settings
from codelens.interface.http.dependencies import (
    HttpProblem,
    build_components,
)
from codelens.interface.http.routers.repositories import router as repositories_router
from codelens.interface.http.routers.reviews import router as reviews_router
from codelens.review.application.commands import ReviewNotFoundError
from codelens.review.domain.agent_run import InvalidAgentRunStateError
from codelens.shared.domain.errors import (
    DomainError,
    InvalidRepositoryError,
    SnapshotStaleError,
)

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _validated_host(raw_host: str) -> str | None:
    try:
        parsed = urlsplit(f"//{raw_host}")
        _port = parsed.port
    except ValueError:
        return None
    if (
        not raw_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed.hostname


def _validated_origin_host(raw_origin: str) -> str | None:
    try:
        parsed = urlsplit(raw_origin)
        _port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed.hostname


class LocalHttpSafetyMiddleware:
    """Reject untrusted Host/Origin and non-JSON command requests before routing."""

    def __init__(self, app: ASGIApp, *, configured_host: str) -> None:
        self._app = app
        self._allowed_hosts = {*_LOOPBACK_HOSTS, configured_host}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/api/health":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = _validated_host(headers.get("host", ""))
        if host not in self._allowed_hosts:
            await JSONResponse(
                {"code": "invalid_host", "message": "The Host header is not allowed."},
                status_code=400,
            )(scope, receive, send)
            return
        origin = headers.get("origin")
        if origin is not None:
            if _validated_origin_host(origin) not in self._allowed_hosts:
                await JSONResponse(
                    {"code": "invalid_origin", "message": "The Origin header is not allowed."},
                    status_code=403,
                )(scope, receive, send)
                return
        method = str(scope.get("method", "GET")).upper()
        if method in _STATE_CHANGING_METHODS:
            content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
            if content_type != "application/json":
                await JSONResponse(
                    {
                        "code": "unsupported_media_type",
                        "message": "Command requests require application/json.",
                    },
                    status_code=415,
                )(scope, receive, send)
                return
        await self._app(scope, receive, send)


def _domain_problem(error: DomainError) -> tuple[int, str]:
    if isinstance(error, InvalidRepositoryError):
        return 422, "The repository or revision is invalid."
    if isinstance(error, SnapshotStaleError):
        return 409, "The repository changed while its review input was captured."
    if isinstance(error, ReviewNotFoundError):
        return 404, "The review does not exist."
    if isinstance(error, InvalidAgentRunStateError):
        return 409, "The review state does not allow this operation."
    return 400, "The request violates a domain rule."


def create_app(settings: Settings) -> FastAPI:
    """Compose the HTTP interface from already validated runtime settings."""

    components = build_components(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await components.start()
        try:
            yield
        finally:
            await components.close()

    app = FastAPI(title="CodeLens Review API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.components = components
    app.add_middleware(LocalHttpSafetyMiddleware, configured_host=settings.host)

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, error: DomainError) -> JSONResponse:
        status_code, message = _domain_problem(error)
        return JSONResponse(
            {"code": error.code, "message": message},
            status_code=status_code,
        )

    @app.exception_handler(HttpProblem)
    async def handle_http_problem(_request: Request, error: HttpProblem) -> JSONResponse:
        return JSONResponse(
            {"code": error.code, "message": error.message},
            status_code=error.status_code,
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Report process readiness without exposing environment details."""

        return {"status": "ready", "auth": settings.auth}

    app.include_router(repositories_router)
    app.include_router(reviews_router)
    return app

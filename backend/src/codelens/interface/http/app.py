import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from codelens.bootstrap.settings import Settings
from codelens.interface.http.dependencies import (
    HttpComponents,
    HttpProblem,
    build_components,
    initialize_plugins,
)
from codelens.interface.http.routers.plugins import router as plugins_router
from codelens.interface.http.routers.repositories import router as repositories_router
from codelens.interface.http.routers.review_profiles import router as review_profiles_router
from codelens.interface.http.routers.reviewer_catalog import router as reviewer_catalog_router
from codelens.interface.http.routers.reviewer_prompts import router as reviewer_prompts_router
from codelens.interface.http.routers.reviews import router as reviews_router
from codelens.interface.http.routers.settings import router as settings_router
from codelens.interface.http.routers.trigger_events import router as trigger_events_router
from codelens.interface.http.routers.webhooks import router as webhooks_router
from codelens.review.application.commands import ReviewNotFoundError
from codelens.review.domain.agent_run import InvalidAgentRunStateError
from codelens.review.domain.review_profile import (
    ReviewProfileDefaultRequiredError,
    ReviewProfileNotFoundError,
    ReviewProfileRevisionConflictError,
)
from codelens.reviewer_catalog.application.provider_settings import ModelGatewayNotFoundError
from codelens.shared.domain.errors import (
    DomainError,
    FilesystemBrowseError,
    InvalidRepositoryError,
    SnapshotStaleError,
)

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOGGER = logging.getLogger("uvicorn.error")


class HttpContentMiddleware:
    """Require JSON for state-changing HTTP commands."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        method = str(scope.get("method", "")).upper()
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
    if isinstance(error, FilesystemBrowseError):
        return 422, "The directory cannot be browsed."
    if isinstance(error, SnapshotStaleError):
        return 409, "The repository changed while its review input was captured."
    if isinstance(error, ReviewNotFoundError):
        return 404, "The review does not exist."
    if isinstance(error, ModelGatewayNotFoundError):
        return 404, "The model gateway does not exist."
    if isinstance(error, InvalidAgentRunStateError):
        return 409, "The review state does not allow this operation."
    if isinstance(error, ReviewProfileNotFoundError):
        return 404, "The review profile does not exist."
    if isinstance(error, ReviewProfileRevisionConflictError):
        return 409, "The review profile changed; reload it before retrying."
    if isinstance(error, ReviewProfileDefaultRequiredError):
        return 409, "At least one review profile must remain the default."
    return 400, "The request violates a domain rule."


def create_app_with_components(
    settings: Settings,
    components: HttpComponents,
    *,
    manage_components: bool = True,
) -> FastAPI:
    """Compose HTTP routes around pre-built components.

    Standalone API applications own component startup and shutdown. The unified backend passes
    ``manage_components=False`` because its outer lifecycle also owns the Worker and must avoid
    running migrations twice or closing shared resources while the scheduler is active.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if manage_components:
            await components.start()
            await initialize_plugins(components)
        try:
            yield
        finally:
            if manage_components:
                await components.close()

    app = FastAPI(title="CodeLens Review API", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.components = components
    app.add_middleware(HttpContentMiddleware)

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

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _error: Exception) -> JSONResponse:
        """Record unhandled request failures without persisting request bodies or credentials."""

        _LOGGER.exception(
            "Unhandled HTTP request error",
            extra={"method": request.method, "path": request.url.path},
        )
        return JSONResponse(
            {"code": "internal_error", "message": "An internal server error occurred."},
            status_code=500,
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Report process readiness without exposing environment details."""

        return {"status": "ready"}

    app.include_router(repositories_router)
    app.include_router(review_profiles_router)
    app.include_router(reviews_router)
    app.include_router(settings_router)
    app.include_router(reviewer_prompts_router)
    app.include_router(reviewer_catalog_router)
    app.include_router(plugins_router)
    app.include_router(webhooks_router)
    app.include_router(trigger_events_router)
    return app


def create_app(settings: Settings) -> FastAPI:
    """Compose the HTTP interface from already validated runtime settings."""

    components = build_components(settings)
    return create_app_with_components(settings, components)

from fastapi import FastAPI

from codelens.bootstrap.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    """Compose the HTTP interface from already validated runtime settings."""

    app = FastAPI(title="CodeLens Review API", version="0.1.0")
    app.state.settings = settings

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Report process readiness without exposing environment details."""

        return {"status": "ready", "auth": settings.auth}

    return app


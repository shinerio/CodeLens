from pathlib import Path
from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validate runtime configuration before any server or Worker starts."""

    model_config = SettingsConfigDict(env_prefix="CODELENS_", env_nested_delimiter="__")

    data_dir: Path = Path()
    prompt_dir: Path = Path()
    host: str = "127.0.0.1"
    port: int = 8800
    auth: Literal["none"] = "none"
    max_workers: int = 1
    max_active_reviews: int = 4
    max_active_agent_runs: int = 8
    max_agent_runs_per_review: int = 4
    repository_roots: tuple[Path, ...] = ()
    database_url: str | None = None
    initialize_schema: bool = True

    @model_validator(mode="before")
    @classmethod
    def resolve_default_paths(cls, data: dict) -> dict:
        """Resolve data_dir and prompt_dir relative to the project root."""
        # Project root = parent of backend/
        project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        if "data_dir" not in data or data["data_dir"] is None:
            data["data_dir"] = project_root / "data"
        if "prompt_dir" not in data or data["prompt_dir"] is None:
            data["prompt_dir"] = project_root / "prompts"
        return data

    @field_validator("repository_roots")
    @classmethod
    def normalize_roots(cls, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        """Normalize configured repository boundaries to canonical absolute paths."""

        return tuple(root.expanduser().resolve() for root in roots)

    @model_validator(mode="after")
    def validate_single_user_runtime(self) -> Self:
        """Fail closed for remote unauthenticated binds and unsupported Worker counts."""

        if self.host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            raise ValueError("auth=none requires a loopback host")
        # 0.0.0.0 binding requires explicit repository roots for security
        if self.host == "0.0.0.0" and not self.repository_roots:
            raise ValueError("binding to 0.0.0.0 requires explicit repository roots")
        # plugin install paths are added dynamically at runtime
        if self.max_workers != 1:
            raise ValueError("the first release supports exactly one Worker")
        if self.max_active_reviews < 1 or self.max_active_agent_runs < 1:
            raise ValueError("review and Agent concurrency limits must be positive")
        if not 1 <= self.max_agent_runs_per_review <= self.max_active_agent_runs:
            raise ValueError("per-review Agent limit must not exceed the global limit")
        return self

    @property
    def resolved_database_url(self) -> str:
        """Return the injected database URL or the contained local SQLite default."""

        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir / 'codelens.sqlite3'}"

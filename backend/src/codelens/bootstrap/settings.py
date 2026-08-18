import tomllib
from pathlib import Path
from typing import Self, cast

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_repository_roots(config_path: Path) -> tuple[Path, ...]:
    """Load repository boundaries from the repository-owned runtime config."""

    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load runtime settings: {config_path}") from error
    if set(payload) != {"repository"} or set(payload["repository"]) != {"roots"}:
        raise ValueError("runtime settings must contain only [repository] roots")
    roots = payload["repository"]["roots"]
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        raise ValueError("runtime repository roots must be an array of paths")
    if any(not root for root in roots):
        raise ValueError("runtime repository roots must not contain empty paths")
    project_root = config_path.resolve().parent.parent
    return tuple(
        (project_root / root if not Path(root).is_absolute() else Path(root)).expanduser()
        for root in roots
    )


class Settings(BaseSettings):
    """Validate runtime configuration before any server or Worker starts."""

    model_config = SettingsConfigDict(env_prefix="CODELENS_", env_nested_delimiter="__")

    data_dir: Path = Path()
    prompt_dir: Path = Path()
    file_exclusion_config: Path = Path()
    web_settings_defaults_config: Path = Path()
    repository_settings_config: Path = Path()
    host: str = "0.0.0.0"
    port: int = 8800
    max_workers: int = 1
    max_active_reviews: int = 4
    max_active_agent_runs: int = 8
    max_agent_runs_per_review: int = 4
    repository_roots: tuple[Path, ...] = ()
    database_url: str | None = None
    initialize_schema: bool = True

    @model_validator(mode="before")
    @classmethod
    def resolve_default_paths(cls, data: dict[str, object]) -> dict[str, object]:
        """Resolve repository-owned defaults and runtime repository boundaries."""
        project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        if "data_dir" not in data or data["data_dir"] is None:
            data["data_dir"] = project_root / "data"
        if "prompt_dir" not in data or data["prompt_dir"] is None:
            data["prompt_dir"] = project_root / "prompts"
        if "file_exclusion_config" not in data or data["file_exclusion_config"] is None:
            data["file_exclusion_config"] = project_root / "conf" / "file-exclusions.toml"
        if (
            "web_settings_defaults_config" not in data
            or data["web_settings_defaults_config"] is None
        ):
            data["web_settings_defaults_config"] = (
                project_root / "conf" / "web-settings-defaults.toml"
            )
        if (
            "repository_settings_config" not in data
            or data["repository_settings_config"] is None
        ):
            data["repository_settings_config"] = (
                project_root / "conf" / "runtime-settings.toml"
            )
        if "repository_roots" not in data or data["repository_roots"] is None:
            data["repository_roots"] = _load_repository_roots(
                Path(cast(str | Path, data["repository_settings_config"]))
            )
        return data

    @field_validator("repository_roots")
    @classmethod
    def normalize_roots(cls, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        """Normalize configured repository boundaries to canonical absolute paths."""

        return tuple(root.expanduser().resolve() for root in roots)

    @model_validator(mode="after")
    def validate_runtime_limits(self) -> Self:
        """Validate process and Review concurrency limits."""

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

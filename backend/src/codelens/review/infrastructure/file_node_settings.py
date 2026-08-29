"""Atomic JSON persistence for process-level resource limits."""

import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict, cast

from codelens.bootstrap.node_settings import NodeSettings


class _NodeSettingsPayload(TypedDict):
    memory_limit_mb: int
    memory_check_interval_seconds: float
    memory_cleanup_threshold_ratio: float
    memory_reject_threshold_ratio: float
    max_active_reviews: int
    max_active_agent_runs: int
    max_agent_runs_per_review: int


_INT_FIELDS = (
    "memory_limit_mb",
    "max_active_reviews",
    "max_active_agent_runs",
    "max_agent_runs_per_review",
)

_FLOAT_FIELDS = (
    "memory_check_interval_seconds",
    "memory_cleanup_threshold_ratio",
    "memory_reject_threshold_ratio",
)


class FilesystemNodeSettingsStore:
    """Store node-level settings in the local CodeLens data directory."""

    def __init__(self, data_dir: Path, defaults: NodeSettings | None = None) -> None:
        self._path = data_dir.expanduser().resolve() / "node-settings.json"
        self._defaults = defaults or NodeSettings()

    def get_node_settings(self) -> NodeSettings:
        """Load persisted settings, using product defaults before first save."""

        if not self._path.exists():
            return self._defaults
        raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("node settings are invalid")
        payload = cast(dict[object, object], raw)
        kwargs: dict[str, int | float] = {}
        defaults = self._defaults
        for field in _INT_FIELDS:
            value = payload.get(field, getattr(defaults, field))
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"node settings field {field} is invalid")
            kwargs[field] = value
        for field in _FLOAT_FIELDS:
            value = payload.get(field, getattr(defaults, field))
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"node settings field {field} is invalid")
            kwargs[field] = float(value)
        return NodeSettings(**kwargs)  # type: ignore[arg-type]

    def save_node_settings(self, settings: NodeSettings) -> None:
        """Write a complete settings document and atomically replace the old one."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".node-settings-",
            suffix=".tmp",
        )
        temporary = Path(name)
        payload: _NodeSettingsPayload = {
            "memory_limit_mb": settings.memory_limit_mb,
            "memory_check_interval_seconds": settings.memory_check_interval_seconds,
            "memory_cleanup_threshold_ratio": settings.memory_cleanup_threshold_ratio,
            "memory_reject_threshold_ratio": settings.memory_reject_threshold_ratio,
            "max_active_reviews": settings.max_active_reviews,
            "max_active_agent_runs": settings.max_active_agent_runs,
            "max_agent_runs_per_review": settings.max_agent_runs_per_review,
        }
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

"""Domain models for the reporting bounded context."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PluginManifest:
    """Declare a report plugin's identity, entry point and configuration schema.

    The manifest is the stable contract between CodeLens and externally installed
    plugins. It is validated at install time and re-read at every sink load.
    """

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    entry_point: str
    config_schema: dict = field(default_factory=dict)
    min_codelens_version: str | None = None


@dataclass(frozen=True)
class PluginRecord:
    """Persisted state of one installed plugin.

    Built-in plugins have ``install_path=None`` and ``is_builtin=True``; they
    cannot be uninstalled. External plugins store their on-disk install path.
    """

    plugin_id: str
    manifest: PluginManifest
    is_enabled: bool
    is_builtin: bool
    install_path: str | None
    config: dict = field(default_factory=dict)
    auto_export: bool = False


@dataclass(frozen=True)
class ExportResult:
    """Outcome of one plugin export attempt for one review task.

    A failed export carries ``error`` and ``output_path=None``; callers should
    never assume the path exists without checking ``success``.
    """

    plugin_id: str
    task_id: str
    success: bool
    output_path: str | None
    error: str | None
    exported_at: datetime


@dataclass(frozen=True)
class ExportHistoryEntry:
    """One persisted export attempt, used for listing prior results."""

    plugin_id: str
    task_id: str
    success: bool
    output_path: str | None
    error: str | None
    exported_at: datetime

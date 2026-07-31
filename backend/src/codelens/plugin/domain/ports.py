"""Ports (interfaces) for the plugin bounded context.

Ports are defined as ``Protocol`` classes so that plugins and adapters can
satisfy them via structural subtyping without importing CodeLens internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from codelens.plugin.domain.models import (
    ExportHistoryEntry,
    ExportResult,
    HookEvent,
    PluginManifest,
    PluginRecord,
)


class ReviewCreatorPort(Protocol):
    """Anti-corruption layer: the plugin context requests review creation
    without importing ``review.application`` directly. The bootstrap layer
    injects a concrete adapter that bridges to ``CreateReviewHandler``.
    """

    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        selected_agents: tuple[str, ...],
        prompt_locale: str,
        external_context: dict[str, Any] | None = None,
    ) -> str:
        """Create a review and return the task_id.

        ``external_context`` carries platform routing information injected by
        the trigger (e.g. GitHub webhook payload fields). Local triggers pass
        ``None``.
        """
        ...


class TriggerSinkPort(Protocol):
    """Receive an event and decide whether to create a review.

    Each trigger plugin implements this protocol. The orchestrator calls
    ``handle_event`` when a matching event arrives. Implementations must not
    raise; they return the task_id or ``None`` when the event is debounced
    or filtered.
    """

    @property
    def trigger_id(self) -> str:
        """Stable identifier matching the plugin manifest ``plugin_id``."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for UI display."""
        ...

    async def handle_event(
        self,
        event: HookEvent,
        repository_path: Path,
        config: dict[str, Any],
        event_payload: dict[str, Any],
        external_context: dict[str, Any] | None = None,
    ) -> str | None:
        """Process one event. Return the created task_id or ``None``."""
        ...


class ReportSinkPort(Protocol):
    """Deliver a finding export envelope to one output target.

    A sink is the extension point for report plugins. Implementations must
    not raise; failures are captured in the returned ``ExportResult``.
    """

    @property
    def sink_id(self) -> str:
        """Return the stable identifier matching the plugin manifest."""
        ...

    @property
    def display_name(self) -> str:
        """Return a human-readable name for UI display."""
        ...

    async def export(
        self,
        envelope: Any,  # FindingExportEnvelope; typed as Any to avoid import cycle
        config: dict[str, Any],
        repository_path: Path,
    ) -> ExportResult:
        """Deliver the envelope and return a structured result."""
        ...


class PluginStorePort(Protocol):
    """Persist plugin installation and configuration state."""

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        """Return all installed plugins (built-in and external)."""
        ...

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        """Return one plugin record or ``None`` if not installed."""
        ...

    async def save_plugin(self, record: PluginRecord) -> None:
        """Create or update a plugin record atomically."""
        ...

    async def delete_plugin(self, plugin_id: str) -> bool:
        """Remove an external plugin record; return ``False`` if not found."""
        ...


class PluginInstallerPort(Protocol):
    """Install validated external plugin code into the application data directory."""

    async def install(
        self,
        git_url: str,
        ref: str | None = None,
    ) -> PluginManifest:
        """Install one Git-backed plugin and return its validated manifest."""
        ...

    async def update(
        self,
        git_url: str,
        install_path: Path,
        ref: str | None = None,
    ) -> PluginManifest:
        """Update an installed plugin by cloning a new version and swapping directories."""
        ...


class PluginCachePort(Protocol):
    """Invalidate executable plugin instances after lifecycle changes."""

    def invalidate(self, plugin_id: str) -> None:
        """Discard cached code and instances for one plugin installation."""
        ...


class PluginLoaderPort(Protocol):
    """Load a report sink instance from a plugin manifest and install path."""

    def load_sink(
        self,
        manifest: PluginManifest,
        install_path: Path,
    ) -> ReportSinkPort:
        """Instantiate and return the sink declared by the manifest."""
        ...

    def invalidate(self, plugin_id: str) -> None:
        """Discard cached code and instances for one plugin installation."""
        ...


class TriggerPluginLoaderPort(Protocol):
    """Load trigger plugin implementations by ``plugin_id``.

    The loader abstracts the mechanism (built-in registry vs. importlib
    external loading) so the application layer remains independent of
    specific plugin implementations.
    """

    def load_plugin(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
        *,
        manifest: PluginManifest | None = None,
        install_path: Path | None = None,
    ) -> TriggerSinkPort:
        """Load and instantiate a trigger plugin by its ID.

        ``manifest`` and ``install_path`` are required for external plugins
        and ignored for built-in ones.
        """
        ...

    def invalidate(self, plugin_id: str) -> None:
        """Discard cached code and instances for one plugin installation."""
        ...


class HookInstallerPort(Protocol):
    """Manage CodeLens-owned Git hook fragments for a validated repository."""

    async def install_hooks(
        self,
        repository_path: Path,
        events: tuple[HookEvent, ...],
        port: int,
    ) -> None:
        """Install the selected local Git event hooks."""
        ...

    async def uninstall_hooks(self, repository_path: Path) -> None:
        """Remove only CodeLens-owned hook content from the repository."""
        ...

    async def is_installed(self, repository_path: Path) -> dict[HookEvent, bool]:
        """Return the installation state of each supported hook event."""
        ...


class TriggerRepositoryValidatorPort(Protocol):
    """Validate trigger repository access and return its canonical root path."""

    async def validate_repository(self, repository_path: Path) -> Path:
        """Validate one exact Git root against the configured access boundary."""
        ...


class ExportHistoryPort(Protocol):
    """Persist and query export operation history."""

    async def save(self, entry: ExportHistoryEntry) -> None:
        """Persist one export history entry."""
        ...

    async def list_by_task(self, task_id: str) -> list[ExportHistoryEntry]:
        """Return all export history entries for a task, newest first."""
        ...

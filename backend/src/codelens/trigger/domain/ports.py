"""Ports (interfaces) for trigger plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
    TriggerRecord,
)


class ReviewCreatorPort(Protocol):
    """Anti-corruption layer: trigger context requests review creation without
    importing review.application directly. Bootstrap injects the adapter.
    """

    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        selected_agents: tuple[str, ...],
        prompt_locale: str,
    ) -> str:
        """Create a review and return the task_id.

        Args:
            repository_path: Absolute path to the git repository.
            scope_type: One of 'commit', 'branch', 'uncommitted'.
            scope_params: Scope-specific parameters (e.g., base_commit, target_ref).
            selected_agents: Agent IDs to use for the review.
            prompt_locale: Locale for review prompts.

        Returns:
            The created review task_id.

        Raises:
            Exception: If review creation fails.
        """
        ...


class TriggerSinkPort(Protocol):
    """Receive a git/webhook event and decide whether to create a review.

    Each trigger plugin implements this protocol. The orchestrator calls
    ``handle_event`` when a matching event arrives. Implementations must not
    raise; they return the task_id or None (if debounced/skipped).
    """

    @property
    def trigger_id(self) -> str:
        """Stable identifier matching the plugin manifest plugin_id."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for UI display."""
        ...

    async def handle_event(
        self,
        event: HookEvent,
        repository_path: Path,
        config: TriggerConfig,
        event_payload: dict[str, str],
    ) -> str | None:
        """Process one event. Call ReviewCreatorPort internally if appropriate.

        Args:
            event: The git hook event type.
            repository_path: Absolute path to the repository where the event occurred.
            config: The plugin's current configuration.
            event_payload: Event-specific data (e.g., {"commit_sha": "abc123"}).

        Returns:
            The task_id if a review was created, None if skipped (debounced or filtered).
        """
        ...


class TriggerStorePort(Protocol):
    """Persist trigger plugin installation and configuration state."""

    async def list_triggers(self) -> tuple[TriggerRecord, ...]:
        """Return all installed trigger plugins."""
        ...

    async def get_trigger(self, plugin_id: str) -> TriggerRecord | None:
        """Return one trigger plugin record, or None if not found."""
        ...

    async def save_trigger(self, record: TriggerRecord) -> None:
        """Create or update a trigger plugin record atomically."""
        ...

    async def delete_trigger(self, plugin_id: str) -> bool:
        """Remove a trigger plugin record. Return False if not found."""
        ...


class TriggerPluginLoaderPort(Protocol):
    """Load trigger plugin implementations by plugin_id.

    This port abstracts the plugin loading mechanism, allowing the application
    layer to remain independent of specific plugin implementations.
    """

    def load_plugin(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
    ) -> TriggerSinkPort:
        """Load and instantiate a trigger plugin by its ID.

        Args:
            plugin_id: The unique identifier of the plugin to load.
            review_creator: Port for creating reviews (injected into the plugin).

        Returns:
            An instantiated trigger plugin implementing TriggerSinkPort.

        Raises:
            ValueError: If the plugin_id is not supported or cannot be loaded.
        """
        ...

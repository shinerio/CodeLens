"""Orchestrate trigger plugin event dispatch and review creation."""

import logging
from pathlib import Path

from codelens.trigger.domain.models import HookEvent, TriggerRecord
from codelens.trigger.domain.ports import (
    ReviewCreatorPort,
    TriggerPluginLoaderPort,
    TriggerSinkPort,
    TriggerStorePort,
)

_LOGGER = logging.getLogger("codelens.plugin.trigger.orchestrator")


class TriggerOrchestrator:
    """Orchestrate event dispatch to trigger plugins.

    Responsibilities:
    - Receive git hook events from HTTP endpoints
    - Match events to enabled trigger plugins
    - Load and invoke plugin handlers
    - Aggregate results and handle failures gracefully
    """

    def __init__(
        self,
        store: TriggerStorePort,
        review_creator: ReviewCreatorPort,
        plugin_loader: TriggerPluginLoaderPort,
    ) -> None:
        """Initialize the trigger orchestrator.

        Args:
            store: Port for querying trigger plugin state.
            review_creator: Port for creating reviews (injected into plugins).
            plugin_loader: Port for loading plugin implementations.
        """
        self._store = store
        self._review_creator = review_creator
        self._plugin_loader = plugin_loader

    async def handle_event(
        self,
        event: HookEvent,
        repository_path: Path,
        event_payload: dict[str, str],
    ) -> tuple[str | None, ...]:
        """Handle a git hook event by dispatching to matching trigger plugins.

        Finds all enabled trigger plugins that are configured to monitor the
        given repository path, then invokes each plugin's handle_event method.
        Failures in one plugin do not prevent other plugins from executing.

        Args:
            event: The git hook event type (post-commit, pre-push, etc.).
            repository_path: Absolute path to the repository where the event occurred.
            event_payload: Event-specific data (e.g., commit_sha, push_ref).

        Returns:
            Tuple of task IDs created by each plugin (None for plugins that
            didn't create a review or failed).
        """
        # Query all enabled trigger plugins
        all_plugins = await self._store.list_triggers()
        enabled_plugins = [p for p in all_plugins if p.is_enabled]

        if not enabled_plugins:
            _LOGGER.debug("No enabled trigger plugins found")
            return ()

        # Filter plugins configured for this repository
        matching_plugins = [
            p for p in enabled_plugins
            if str(repository_path) in p.config.repository_paths
        ]

        if not matching_plugins:
            _LOGGER.debug(
                "No trigger plugins configured for repository: %s",
                repository_path,
            )
            return ()

        _LOGGER.info(
            "Dispatching %s event to %d plugin(s) for %s",
            event.value,
            len(matching_plugins),
            repository_path,
        )

        # Invoke each matching plugin
        results: list[str | None] = []
        for plugin_record in matching_plugins:
            try:
                result = await self._invoke_plugin(
                    plugin_record, event, repository_path, event_payload
                )
                results.append(result)
            except Exception:
                _LOGGER.exception(
                    "Plugin %s failed to handle %s event",
                    plugin_record.plugin_id,
                    event.value,
                )
                results.append(None)

        return tuple(results)

    async def _invoke_plugin(
        self,
        plugin_record: TriggerRecord,
        event: HookEvent,
        repository_path: Path,
        event_payload: dict[str, str],
    ) -> str | None:
        """Invoke a single trigger plugin's event handler.

        Args:
            plugin_record: The plugin record containing configuration.
            event: The git hook event type.
            repository_path: Absolute path to the repository.
            event_payload: Event-specific data.

        Returns:
            Task ID if a review was created, None otherwise.
        """
        # Load the plugin implementation
        plugin = self._load_plugin(plugin_record)

        # Invoke the plugin's event handler
        task_id = await plugin.handle_event(
            event=event,
            repository_path=repository_path,
            config=plugin_record.config,
            event_payload=event_payload,
        )

        if task_id:
            _LOGGER.info(
                "Plugin %s created review %s for %s event",
                plugin_record.plugin_id,
                task_id,
                event.value,
            )
        else:
            _LOGGER.debug(
                "Plugin %s did not create a review for %s event (debounced or filtered)",
                plugin_record.plugin_id,
                event.value,
            )

        return task_id

    def _load_plugin(self, plugin_record: TriggerRecord) -> TriggerSinkPort:
        """Load and instantiate a trigger plugin implementation.

        Delegates to the injected plugin_loader port to maintain DDD layering.

        Args:
            plugin_record: The plugin record to load.

        Returns:
            Instantiated trigger plugin implementing TriggerSinkPort.

        Raises:
            ValueError: If the plugin type is not supported.
        """
        return self._plugin_loader.load_plugin(
            plugin_record.plugin_id,
            self._review_creator,
        )

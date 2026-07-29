"""Orchestrate trigger plugin event dispatch and review creation."""

import logging
from pathlib import Path
from typing import Any

from codelens.plugin.domain.models import HookEvent, PluginRecord
from codelens.plugin.domain.ports import (
    PluginStorePort,
    ReviewCreatorPort,
    TriggerPluginLoaderPort,
    TriggerSinkPort,
)

_LOGGER = logging.getLogger("codelens.plugin.trigger.orchestrator")


class TriggerOrchestrator:
    """Orchestrate event dispatch to trigger plugins.

    Responsibilities:
    - Receive git hook and webhook events from HTTP endpoints
    - Match events to enabled trigger plugins
    - Load and invoke plugin handlers
    - Aggregate results and handle failures gracefully
    """

    def __init__(
        self,
        store: PluginStorePort,
        review_creator: ReviewCreatorPort,
        plugin_loader: TriggerPluginLoaderPort,
    ) -> None:
        """Initialize the trigger orchestrator.

        Args:
            store: Port for querying plugin state.
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
        event_payload: dict[str, Any],
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
        # Query all plugins with enabled trigger capability
        all_plugins = await self._store.list_plugins()
        enabled_plugins = [p for p in all_plugins if p.trigger_enabled]

        if not enabled_plugins:
            _LOGGER.debug("No enabled trigger plugins found")
            return ()

        # Filter plugins configured for this repository
        matching_plugins = [
            p for p in enabled_plugins
            if str(repository_path) in p.trigger_config.get("repository_paths", [])
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

    async def handle_webhook(
        self,
        platform: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[str | None, ...]:
        """Handle a webhook event by dispatching to matching trigger plugins.

        Finds all enabled trigger plugins that match the given platform,
        then invokes each plugin's handle_event method with WEBHOOK event type.
        Failures in one plugin do not prevent other plugins from executing.

        Args:
            platform: The platform identifier (e.g., "github", "gitlab").
            payload: The webhook payload parsed from JSON.
            headers: HTTP headers from the webhook request.

        Returns:
            Tuple of task IDs created by each plugin (None for plugins that
            didn't create a review or failed).
        """
        # Query all plugins with enabled trigger capability matching the platform
        all_plugins = await self._store.list_plugins()
        matching_plugins = [
            p for p in all_plugins
            if p.trigger_enabled and p.manifest.platform == platform
        ]

        if not matching_plugins:
            _LOGGER.debug(
                "No trigger plugins configured for platform: %s",
                platform,
            )
            return ()

        _LOGGER.info(
            "Dispatching webhook event to %d plugin(s) for platform %s",
            len(matching_plugins),
            platform,
        )

        # Invoke each matching plugin
        results: list[str | None] = []
        for plugin_record in matching_plugins:
            try:
                result = await self._invoke_webhook_plugin(
                    plugin_record, payload, headers
                )
                results.append(result)
            except Exception:
                _LOGGER.exception(
                    "Plugin %s failed to handle webhook event",
                    plugin_record.plugin_id,
                )
                results.append(None)

        return tuple(results)

    async def _invoke_plugin(
        self,
        plugin_record: PluginRecord,
        event: HookEvent,
        repository_path: Path,
        event_payload: dict[str, Any],
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
            config=plugin_record.trigger_config,
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

    async def _invoke_webhook_plugin(
        self,
        plugin_record: PluginRecord,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> str | None:
        """Invoke a single trigger plugin's webhook handler.

        Args:
            plugin_record: The plugin record containing configuration.
            payload: The webhook payload.
            headers: HTTP headers from the webhook request.

        Returns:
            Task ID if a review was created, None otherwise.
        """
        # Load the plugin implementation
        plugin = self._load_plugin(plugin_record)

        # Invoke the plugin's event handler with WEBHOOK event type
        # The plugin is responsible for parsing the payload and extracting
        # repository_path, scope, and external_context
        task_id = await plugin.handle_event(
            event=HookEvent.WEBHOOK,
            repository_path=Path("."),  # Placeholder, plugin will override
            config=plugin_record.trigger_config,
            event_payload={"payload": payload, "headers": headers},
        )

        if task_id:
            _LOGGER.info(
                "Plugin %s created review %s for webhook event",
                plugin_record.plugin_id,
                task_id,
            )
        else:
            _LOGGER.debug(
                "Plugin %s did not create a review for webhook event (filtered or invalid)",
                plugin_record.plugin_id,
            )

        return task_id

    def _load_plugin(self, plugin_record: PluginRecord) -> TriggerSinkPort:
        """Load and instantiate a trigger plugin implementation.

        Delegates to the injected plugin_loader port to maintain DDD layering.

        Args:
            plugin_record: The plugin record to load.

        Returns:
            Instantiated trigger plugin implementing TriggerSinkPort.

        Raises:
            ValueError: If the plugin type is not supported.
        """
        install_path = Path(plugin_record.install_path) if plugin_record.install_path else None
        return self._plugin_loader.load_plugin(
            plugin_record.plugin_id,
            self._review_creator,
            manifest=plugin_record.manifest,
            install_path=install_path,
        )

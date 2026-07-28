"""Manage trigger plugin lifecycle: install, enable, configure, uninstall."""

from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
    TriggerManifest,
    TriggerRecord,
    TriggerType,
)
from codelens.trigger.domain.ports import TriggerStorePort


class TriggerPluginManager:
    """Manage the lifecycle of trigger plugins.

    Responsibilities:
    - Initialize built-in plugins on startup
    - Install/uninstall plugins
    - Enable/disable plugins
    - Update plugin configuration
    - Query plugin state
    """

    BUILTIN_PLUGIN_ID = "local-git-hook"

    def __init__(self, store: TriggerStorePort) -> None:
        """Initialize the trigger plugin manager.

        Args:
            store: Port for persisting trigger plugin state.
        """
        self._store = store

    async def initialize_builtin_plugins(self) -> None:
        """Initialize built-in trigger plugins if not already present.

        Called during application startup to ensure built-in plugins exist.
        """
        existing = await self._store.get_trigger(self.BUILTIN_PLUGIN_ID)
        if existing is not None:
            return

        manifest = TriggerManifest(
            plugin_id=self.BUILTIN_PLUGIN_ID,
            name="Local Git Hook Trigger",
            version="1.0.0",
            description="Automatically trigger code reviews on git commit or push events",
            author="CodeLens Team",
            entry_point="local_hook_trigger:LocalHookTriggerAdapter",
            trigger_type=TriggerType.LOCAL_HOOK,
            supported_events=(HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
            config_schema={
                "type": "object",
                "properties": {
                    "repository_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Absolute paths to repositories to monitor",
                    },
                    "events": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["post-commit", "pre-push"]},
                        "description": "Git events that trigger reviews",
                    },
                    "scope_type": {
                        "type": "string",
                        "enum": ["commit", "branch", "uncommitted"],
                        "description": "Review scope type",
                    },
                    "base_ref": {
                        "type": "string",
                        "description": "Base reference for branch scope (e.g., 'main')",
                    },
                    "target_ref": {
                        "type": "string",
                        "description": "Target reference for branch scope (e.g., 'HEAD')",
                    },
                    "selected_agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Agent IDs to use for reviews",
                    },
                    "prompt_locale": {
                        "type": "string",
                        "enum": ["en", "zh-CN"],
                        "description": "Locale for review prompts",
                    },
                    "debounce_seconds": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Minimum seconds between triggers (0 to disable)",
                    },
                },
            },
        )

        config = TriggerConfig(
            repository_paths=(),
            events=(HookEvent.POST_COMMIT,),
            scope_type="commit",
            base_ref=None,
            target_ref=None,
            selected_agents=(),
            prompt_locale="en",
            debounce_seconds=10,
        )

        record = TriggerRecord(
            plugin_id=self.BUILTIN_PLUGIN_ID,
            manifest=manifest,
            is_enabled=False,
            is_builtin=True,
            install_path=None,
            config=config,
        )

        await self._store.save_trigger(record)

    async def list_plugins(self) -> tuple[TriggerRecord, ...]:
        """List all installed trigger plugins.

        Returns:
            Tuple of all trigger plugin records.
        """
        return await self._store.list_triggers()

    async def get_plugin(self, plugin_id: str) -> TriggerRecord | None:
        """Get a specific trigger plugin by ID.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Plugin record if found, None otherwise.
        """
        return await self._store.get_trigger(plugin_id)

    async def enable_plugin(self, plugin_id: str) -> TriggerRecord | None:
        """Enable a trigger plugin.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_trigger(plugin_id)
        if record is None:
            return None

        updated = TriggerRecord(
            plugin_id=record.plugin_id,
            manifest=record.manifest,
            is_enabled=True,
            is_builtin=record.is_builtin,
            install_path=record.install_path,
            config=record.config,
        )
        await self._store.save_trigger(updated)
        return updated

    async def disable_plugin(self, plugin_id: str) -> TriggerRecord | None:
        """Disable a trigger plugin.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_trigger(plugin_id)
        if record is None:
            return None

        updated = TriggerRecord(
            plugin_id=record.plugin_id,
            manifest=record.manifest,
            is_enabled=False,
            is_builtin=record.is_builtin,
            install_path=record.install_path,
            config=record.config,
        )
        await self._store.save_trigger(updated)
        return updated

    async def update_config(
        self, plugin_id: str, config: TriggerConfig
    ) -> TriggerRecord | None:
        """Update the configuration for a trigger plugin.

        Args:
            plugin_id: Unique identifier of the plugin.
            config: New configuration to apply.

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_trigger(plugin_id)
        if record is None:
            return None

        updated = TriggerRecord(
            plugin_id=record.plugin_id,
            manifest=record.manifest,
            is_enabled=record.is_enabled,
            is_builtin=record.is_builtin,
            install_path=record.install_path,
            config=config,
        )
        await self._store.save_trigger(updated)
        return updated

    async def uninstall_plugin(self, plugin_id: str) -> bool:
        """Uninstall a trigger plugin.

        Built-in plugins cannot be uninstalled.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            True if uninstalled, False if not found or is built-in.
        """
        record = await self._store.get_trigger(plugin_id)
        if record is None:
            return False

        if record.is_builtin:
            return False

        return await self._store.delete_trigger(plugin_id)

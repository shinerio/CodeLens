"""Built-in trigger plugin loader implementation."""

from pathlib import Path

from codelens.plugin.domain.models import PluginManifest
from codelens.plugin.domain.ports import (
    ReviewCreatorPort,
    TriggerPluginLoaderPort,
    TriggerSinkPort,
)
from codelens.plugin.trigger.local_hook.local_hook_trigger import (
    LocalHookTriggerAdapter,
)


class BuiltinTriggerPluginLoader(TriggerPluginLoaderPort):
    """Load built-in trigger plugin implementations.

    This loader supports the built-in local hook trigger plugin.
    External plugins can be added by extending this class or creating
    a composite loader.
    """

    def __init__(self) -> None:
        """Initialize with an empty plugin instance cache."""
        self._instances: dict[str, TriggerSinkPort] = {}

    def load_plugin(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
        *,
        manifest: PluginManifest | None = None,
        install_path: Path | None = None,
    ) -> TriggerSinkPort:
        """Load a built-in trigger plugin by its ID.

        Args:
            plugin_id: The unique identifier of the plugin to load.
            review_creator: Port for creating reviews (injected into the plugin).

        Returns:
            An instantiated trigger plugin implementing TriggerSinkPort.

        Raises:
            ValueError: If the plugin_id is not a supported built-in plugin.
        """
        if plugin_id in self._instances:
            return self._instances[plugin_id]

        if plugin_id == LocalHookTriggerAdapter.TRIGGER_ID:
            instance = LocalHookTriggerAdapter(review_creator)
            self._instances[plugin_id] = instance
            return instance

        raise ValueError(
            f"Unsupported trigger plugin: {plugin_id}. "
            f"Supported built-in plugins: {LocalHookTriggerAdapter.TRIGGER_ID}"
        )

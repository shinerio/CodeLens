"""Built-in trigger plugin loader implementation."""

from codelens.plugin.trigger.local_hook.local_hook_trigger import (
    LocalHookTriggerAdapter,
)
from codelens.trigger.domain.ports import (
    ReviewCreatorPort,
    TriggerPluginLoaderPort,
    TriggerSinkPort,
)


class BuiltinTriggerPluginLoader(TriggerPluginLoaderPort):
    """Load built-in trigger plugin implementations.

    This loader supports the built-in local hook trigger plugin.
    External plugins can be added by extending this class or creating
    a composite loader.
    """

    def load_plugin(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
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
        if plugin_id == LocalHookTriggerAdapter.TRIGGER_ID:
            return LocalHookTriggerAdapter(review_creator)

        raise ValueError(
            f"Unsupported trigger plugin: {plugin_id}. "
            f"Supported built-in plugins: {LocalHookTriggerAdapter.TRIGGER_ID}"
        )

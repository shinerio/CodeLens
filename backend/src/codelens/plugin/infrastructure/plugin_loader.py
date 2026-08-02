"""Composite plugin loader supporting built-in and external plugins.

The loader first attempts to resolve a plugin as a built-in implementation.
If not found, it falls back to dynamic loading via importlib from the
plugin's install path.
"""

import importlib.util
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

import codelens
from codelens.plugin.domain.models import PluginManifest
from codelens.plugin.domain.ports import (
    ReportSinkPort,
    ReviewCreatorPort,
    TriggerSinkPort,
)
from codelens.plugin.domain.versioning import PluginApiVersion, ensure_plugin_compatible
from codelens.plugin.trigger.local_hook.local_hook_trigger import (
    LocalHookTriggerAdapter,
)


class PluginLoadError(Exception):
    """Raised when a plugin manifest's entry point cannot be loaded or validated."""


class CompositePluginLoader:
    """Load plugin instances from built-in registry or external install paths.

    This loader implements a two-stage resolution strategy:
    1. Check if the plugin_id matches a known built-in implementation
    2. Fall back to importlib-based dynamic loading from install_path

    Both trigger and report plugins are supported through separate methods.
    """

    _MODULE_PREFIX = "codelens_ext_plugin_"

    def __init__(self) -> None:
        """Initialize with empty instance caches."""
        self._trigger_instances: dict[str, TriggerSinkPort] = {}
        self._report_instances: dict[str, ReportSinkPort] = {}
        self._generations: dict[str, int] = {}

    def load_plugin(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
        *,
        manifest: PluginManifest | None = None,
        install_path: Path | None = None,
    ) -> TriggerSinkPort:
        """Load a trigger plugin instance.

        Args:
            plugin_id: Unique plugin identifier.
            review_creator: Port for creating reviews (injected into trigger).
            manifest: Plugin manifest (required for external plugins).
            install_path: Plugin install directory (required for external plugins).

        Returns:
            Instantiated trigger plugin implementing TriggerSinkPort.

        Raises:
            ValueError: If plugin_id is not a supported built-in and no install_path provided.
            PluginLoadError: If external plugin cannot be loaded.
        """
        if plugin_id in self._trigger_instances:
            return self._trigger_instances[plugin_id]

        # Try built-in plugins first
        if plugin_id == LocalHookTriggerAdapter.TRIGGER_ID:
            instance = LocalHookTriggerAdapter(review_creator)
            self._trigger_instances[plugin_id] = instance
            return instance

        # Fall back to external plugin loading
        if manifest is None or install_path is None:
            raise ValueError(
                f"Unsupported trigger plugin: {plugin_id}. "
                f"External plugins require manifest and install_path."
            )
        self._ensure_compatible(manifest)

        trigger_cap = manifest.trigger
        if trigger_cap is None:
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} does not declare trigger capability"
            )

        external_instance: TriggerSinkPort = self._load_external_trigger(
            trigger_cap.entry_point,
            install_path,
            review_creator,
            plugin_id,
        )
        self._trigger_instances[plugin_id] = external_instance
        return external_instance

    def load_sink(
        self,
        manifest: PluginManifest,
        install_path: Path,
    ) -> ReportSinkPort:
        """Load a report plugin instance.

        Args:
            manifest: Plugin manifest with report capability.
            install_path: Plugin install directory.

        Returns:
            Instantiated report plugin implementing ReportSinkPort.

        Raises:
            PluginLoadError: If plugin cannot be loaded.
        """
        plugin_id = manifest.plugin_id
        self._ensure_compatible(manifest)
        if plugin_id in self._report_instances:
            return self._report_instances[plugin_id]

        report_cap = manifest.report
        if report_cap is None:
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} does not declare report capability"
            )

        instance = self._load_external_report(report_cap.entry_point, install_path, plugin_id)
        self._report_instances[plugin_id] = instance
        return instance

    def invalidate(self, plugin_id: str) -> None:
        """Discard cached instances and imported modules for one plugin."""

        self._trigger_instances.pop(plugin_id, None)
        self._report_instances.pop(plugin_id, None)
        module_prefix = self._module_cache_prefix(plugin_id)
        for module_name in tuple(sys.modules):
            if module_name == module_prefix or module_name.startswith(f"{module_prefix}_"):
                sys.modules.pop(module_name, None)
        self._generations[plugin_id] = self._generations.get(plugin_id, 0) + 1

    def _load_external_trigger(
        self,
        entry_point: str,
        install_path: Path,
        review_creator: ReviewCreatorPort,
        plugin_id: str,
    ) -> TriggerSinkPort:
        """Load an external trigger plugin via importlib."""
        if ":" not in entry_point:
            raise PluginLoadError(
                f"plugin {plugin_id} entry_point must be 'module:Class', got: {entry_point}"
            )
        module_name, class_name = entry_point.split(":", 1)
        if not module_name or not class_name:
            raise PluginLoadError(
                f"plugin {plugin_id} entry_point has empty module or class"
            )

        module_file = install_path / module_name
        if not module_file.exists() and not module_file.with_suffix(".py").exists():
            resolved = module_file if module_file.exists() else module_file.with_suffix(".py")
            raise PluginLoadError(
                f"plugin {plugin_id} module file not found: {resolved}"
            )
        if not module_file.suffix:
            module_file = module_file.with_suffix(".py")

        cache_key = self._module_cache_key(plugin_id)
        spec = importlib.util.spec_from_file_location(cache_key, module_file)
        if spec is None or spec.loader is None:
            raise PluginLoadError(
                f"plugin {plugin_id} module spec could not be created"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[cache_key] = module

        # Add install_path to sys.path so plugin can import sibling modules.
        # Use append (lowest priority) to avoid shadowing stdlib or third-party packages.
        install_path_str = str(install_path.resolve())
        if install_path_str not in sys.path:
            sys.path.append(install_path_str)

        try:
            source = module_file.read_text(encoding="utf-8")
            exec(compile(source, str(module_file), "exec"), module.__dict__)
        except Exception as error:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} module failed to load: {error}"
            ) from error

        trigger_class = getattr(module, class_name, None)
        if trigger_class is None:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} class '{class_name}' not found in module"
            )
        try:
            instance = trigger_class(review_creator)
        except Exception as error:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} trigger instantiation failed: {error}"
            ) from error

        if not hasattr(instance, "trigger_id") or not hasattr(instance, "handle_event"):
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} trigger does not implement TriggerSinkPort"
            )
        return instance

    def _load_external_report(
        self,
        entry_point: str,
        install_path: Path,
        plugin_id: str,
    ) -> ReportSinkPort:
        """Load an external report plugin via importlib."""
        if ":" not in entry_point:
            raise PluginLoadError(
                f"plugin {plugin_id} entry_point must be 'module:Class', got: {entry_point}"
            )
        module_name, class_name = entry_point.split(":", 1)
        if not module_name or not class_name:
            raise PluginLoadError(
                f"plugin {plugin_id} entry_point has empty module or class"
            )

        module_file = install_path / module_name
        if not module_file.exists() and not module_file.with_suffix(".py").exists():
            resolved = module_file if module_file.exists() else module_file.with_suffix(".py")
            raise PluginLoadError(
                f"plugin {plugin_id} module file not found: {resolved}"
            )
        if not module_file.suffix:
            module_file = module_file.with_suffix(".py")

        cache_key = self._module_cache_key(plugin_id)
        spec = importlib.util.spec_from_file_location(cache_key, module_file)
        if spec is None or spec.loader is None:
            raise PluginLoadError(
                f"plugin {plugin_id} module spec could not be created"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[cache_key] = module

        # Add install_path to sys.path so plugin can import sibling modules.
        # Use append (lowest priority) to avoid shadowing stdlib or third-party packages.
        install_path_str = str(install_path.resolve())
        if install_path_str not in sys.path:
            sys.path.append(install_path_str)

        try:
            source = module_file.read_text(encoding="utf-8")
            exec(compile(source, str(module_file), "exec"), module.__dict__)
        except Exception as error:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} module failed to load: {error}"
            ) from error

        sink_class = getattr(module, class_name, None)
        if sink_class is None:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} class '{class_name}' not found in module"
            )
        try:
            sink = sink_class()
        except Exception as error:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} sink instantiation failed: {error}"
            ) from error

        if not hasattr(sink, "sink_id") or not hasattr(sink, "export"):
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {plugin_id} sink does not implement ReportSinkPort"
            )
        return sink

    def _module_cache_key(self, plugin_id: str) -> str:
        generation = self._generations.get(plugin_id, 0)
        return f"{self._module_cache_prefix(plugin_id)}_{generation}"

    @staticmethod
    def _ensure_compatible(manifest: PluginManifest) -> None:
        """Revalidate persisted metadata immediately before untrusted code loads."""

        try:
            plugin_version = Version(manifest.version)
            if manifest.plugin_api_version is PluginApiVersion.V2:
                if manifest.min_codelens_version is None or plugin_version.major < 2:
                    raise PluginLoadError("plugin declares an invalid v2 compatibility range")
            ensure_plugin_compatible(
                plugin_api_version=manifest.plugin_api_version,
                minimum_codelens_version=Version(manifest.min_codelens_version or "0"),
                current_codelens_version=Version(codelens.__version__),
            )
        except (InvalidVersion, ValueError) as error:
            raise PluginLoadError(
                f"plugin is incompatible with this CodeLens host: {error}"
            ) from error

    @classmethod
    def _module_cache_prefix(cls, plugin_id: str) -> str:
        return cls._MODULE_PREFIX + plugin_id.replace("-", "_")

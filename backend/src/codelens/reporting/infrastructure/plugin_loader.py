"""Load report plugin sinks from external directories via importlib."""

import importlib.util
import sys
from pathlib import Path

from codelens.reporting.domain.models import PluginManifest
from codelens.reporting.domain.ports import ReportSinkPort


class PluginLoadError(Exception):
    """Raised when a plugin manifest's entry point cannot be loaded or validated."""


class ImportlibPluginLoader:
    """Instantiate a sink from a plugin manifest's ``entry_point`` field.

    The entry point format is ``"module_name:ClassName"``, resolved relative to
    the plugin's install path. Loaded modules are cached in ``sys.modules``
    under a namespaced key to avoid collisions with CodeLens internals.
    """

    _MODULE_PREFIX = "codelens_ext_plugin_"

    def load_sink(self, manifest: PluginManifest, install_path: Path) -> ReportSinkPort:
        entry = manifest.entry_point
        if ":" not in entry:
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} entry_point must be 'module:Class', got: {entry}"
            )
        module_name, class_name = entry.split(":", 1)
        if not module_name or not class_name:
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} entry_point has empty module or class"
            )

        module_file = install_path / module_name
        if not module_file.exists() and not module_file.with_suffix(".py").exists():
            resolved = module_file if module_file.exists() else module_file.with_suffix(".py")
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} module file not found: {resolved}"
            )
        if not module_file.suffix:
            module_file = module_file.with_suffix(".py")

        cache_key = self._MODULE_PREFIX + manifest.plugin_id.replace("-", "_")
        spec = importlib.util.spec_from_file_location(cache_key, module_file)
        if spec is None or spec.loader is None:
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} module spec could not be created"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[cache_key] = module
        try:
            spec.loader.exec_module(module)
        except Exception as error:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} module failed to load: {error}"
            ) from error

        sink_class = getattr(module, class_name, None)
        if sink_class is None:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} class '{class_name}' not found in module"
            )
        try:
            sink = sink_class()
        except Exception as error:
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} sink instantiation failed: {error}"
            ) from error

        if not hasattr(sink, "sink_id") or not hasattr(sink, "export"):
            sys.modules.pop(cache_key, None)
            raise PluginLoadError(
                f"plugin {manifest.plugin_id} sink does not implement ReportSinkPort"
            )
        return sink

"""Unified plugin lifecycle management.

The PluginManager orchestrates installation, configuration, and removal of
plugins. Each plugin declares a platform and optional trigger/report capabilities.
Built-in plugins have no constraints; external plugins require trigger to be
enabled before report can be enabled.
"""

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for

from codelens.plugin.domain.models import (
    PluginCapabilityError,
    PluginConfigurationError,
    PluginInstallError,
    PluginManifest,
    PluginRecord,
    ReportCapability,
    TriggerCapability,
    validate_capability_toggle,
)
from codelens.plugin.domain.ports import (
    PluginCachePort,
    PluginInstallerPort,
    PluginStorePort,
)


class PluginManager:
    """Manage plugin lifecycle: install, enable, configure, uninstall.

    Responsibilities:
    - Initialize built-in plugins on startup
    - Install external plugins from Git repositories
    - Enable/disable trigger and report capabilities independently
    - Update trigger and report configuration separately
    - Manage auto-export settings for report capability
    - Uninstall plugins and clean up resources
    """

    BUILTIN_PLUGIN_ID = "local"

    def __init__(
        self,
        store: PluginStorePort,
        installer: PluginInstallerPort,
        plugins_dir: Path,
        plugin_cache: PluginCachePort | None = None,
    ) -> None:
        """Initialize the plugin manager.

        Args:
            store: Port for persisting plugin state.
            installer: Git-based plugin installer.
            plugins_dir: Directory where external plugins are installed.
        """
        self._store = store
        self._installer = installer
        self._plugins_dir = plugins_dir
        self._plugin_cache = plugin_cache

    async def initialize_builtin(self) -> None:
        """Initialize built-in plugins if not already present.

        Called during application startup to ensure built-in plugins exist.
        The built-in "local" plugin provides both trigger (local-hook) and
        report (file-export) capabilities.
        """
        existing = await self._store.get_plugin(self.BUILTIN_PLUGIN_ID)
        if existing is not None:
            return

        manifest = PluginManifest(
            plugin_id=self.BUILTIN_PLUGIN_ID,
            name="Local Development Plugin",
            version="1.0.0",
            description=(
                "Local git hook trigger and file-based report export "
                "for development workflows"
            ),
            author="CodeLens Team",
            platform="local",
            capabilities={
                "trigger": TriggerCapability(
                    trigger_type="local-hook",
                    supported_events=("post-commit", "pre-push"),
                    entry_point="local_hook_trigger:LocalHookTriggerAdapter",
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
                                "type": ["string", "null"],
                                "description": "Base reference for branch scope (e.g., 'main')",
                            },
                            "target_ref": {
                                "type": ["string", "null"],
                                "description": "Target reference for branch scope (e.g., 'HEAD')",
                            },
                            "selected_agents": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
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
                ),
                "report": ReportCapability(
                    entry_point="local_file_sink:LocalFileExportSink",
                    config_schema={
                        "type": "object",
                        "properties": {
                            "output_dir": {
                                "type": "string",
                                "default": "CodeLensReview",
                                "description": (
                                    "Output directory name relative "
                                    "to reviewed repo root"
                                ),
                            },
                            "formats": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["json", "markdown"]},
                                "default": ["json", "markdown"],
                            },
                        },
                    },
                ),
            },
        )

        record = PluginRecord(
            plugin_id=self.BUILTIN_PLUGIN_ID,
            manifest=manifest,
            is_builtin=True,
            install_path=None,
            trigger_enabled=False,
            report_enabled=True,
            report_auto_export=False,
            trigger_config={
                "repository_paths": [],
                "events": ["post-commit"],
                "scope_type": "commit",
                "base_ref": None,
                "target_ref": None,
                "selected_agents": ["correctness:v1"],
                "prompt_locale": "en",
                "debounce_seconds": 10,
            },
            report_config={
                "output_dir": "CodeLensReview",
                "formats": ["json", "markdown"],
            },
        )

        await self._store.save_plugin(record)

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        """List all installed plugins.

        Returns:
            Tuple of all plugin records.
        """
        return await self._store.list_plugins()

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        """Get a specific plugin by ID.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Plugin record if found, None otherwise.
        """
        return await self._store.get_plugin(plugin_id)

    async def install_from_git(self, git_url: str, ref: str | None = None) -> PluginRecord:
        """Install a plugin from a Git repository.

        Args:
            git_url: Git repository URL.
            ref: Optional Git reference (branch, tag, commit).

        Returns:
            Plugin record of the installed plugin.

        Raises:
            PluginInstallError: If installation fails.
        """
        manifest = await self._installer.install(git_url, ref)
        install_path = str(self._plugins_dir / manifest.plugin_id)

        # Extract default configs from capability schemas
        trigger_config = {}
        if "trigger" in manifest.capabilities:
            trigger_cap = manifest.capabilities["trigger"]
            trigger_config = self._extract_defaults(trigger_cap.config_schema)

        report_config = {}
        if "report" in manifest.capabilities:
            report_cap = manifest.capabilities["report"]
            report_config = self._extract_defaults(report_cap.config_schema)

        record = PluginRecord(
            plugin_id=manifest.plugin_id,
            manifest=manifest,
            is_builtin=False,
            install_path=install_path,
            trigger_enabled=False,
            report_enabled=False,
            report_auto_export=False,
            trigger_config=trigger_config,
            report_config=report_config,
        )

        await self._store.save_plugin(record)
        self._invalidate_plugin(manifest.plugin_id)
        return record

    async def enable_trigger(self, plugin_id: str) -> PluginRecord | None:
        """Enable the trigger capability of a plugin.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None

        self._require_capability(record, "trigger")
        self.validate_trigger_config(record, record.trigger_config)

        updated = replace(record, trigger_enabled=True)
        await self._store.save_plugin(updated)
        return updated

    async def disable_trigger(self, plugin_id: str) -> PluginRecord | None:
        """Disable the trigger capability of a plugin.

        For external plugins, disabling trigger also disables report (cascade).

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None

        self._require_capability(record, "trigger")

        # For external plugins, report depends on trigger
        report_enabled = record.report_enabled
        if not record.is_builtin and report_enabled:
            report_enabled = False

        updated = replace(
            record,
            trigger_enabled=False,
            report_enabled=report_enabled,
        )
        await self._store.save_plugin(updated)
        return updated

    async def enable_report(self, plugin_id: str) -> PluginRecord | None:
        """Enable the report capability of a plugin.

        For external plugins, trigger must be enabled first.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Updated plugin record if found, None otherwise.

        Raises:
            ValueError: If external plugin's trigger is not enabled.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None

        self._require_capability(record, "report")
        self.validate_report_config(record, record.report_config)

        # Validate capability dependency for external plugins
        validate_capability_toggle(record, enable_report=True)

        updated = replace(record, report_enabled=True)
        await self._store.save_plugin(updated)
        return updated

    async def disable_report(self, plugin_id: str) -> PluginRecord | None:
        """Disable the report capability of a plugin.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None

        self._require_capability(record, "report")

        updated = replace(record, report_enabled=False)
        await self._store.save_plugin(updated)
        return updated

    async def update_trigger_config(
        self, plugin_id: str, config: dict[str, Any]
    ) -> PluginRecord | None:
        """Update the trigger configuration of a plugin.

        Args:
            plugin_id: Unique identifier of the plugin.
            config: New trigger configuration (merged with existing).

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None

        merged = {**record.trigger_config, **config}
        self.validate_trigger_config(record, merged)
        updated = replace(record, trigger_config=merged)
        await self._store.save_plugin(updated)
        return updated

    async def update_report_config(
        self, plugin_id: str, config: dict[str, Any]
    ) -> PluginRecord | None:
        """Update the report configuration of a plugin.

        Args:
            plugin_id: Unique identifier of the plugin.
            config: New report configuration (merged with existing).

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None

        merged = {**record.report_config, **config}
        self.validate_report_config(record, merged)
        updated = replace(record, report_config=merged)
        await self._store.save_plugin(updated)
        return updated

    async def set_auto_export(self, plugin_id: str, enabled: bool) -> PluginRecord | None:
        """Enable or disable auto-export for a plugin's report capability.

        Args:
            plugin_id: Unique identifier of the plugin.
            enabled: Whether to enable auto-export.

        Returns:
            Updated plugin record if found, None otherwise.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None

        self._require_capability(record, "report")

        updated = replace(record, report_auto_export=enabled)
        await self._store.save_plugin(updated)
        return updated

    async def uninstall_plugin(self, plugin_id: str) -> bool:
        """Uninstall a plugin.

        Built-in plugins cannot be uninstalled. External plugins have their
        installation directory removed.

        Args:
            plugin_id: Unique identifier of the plugin.

        Returns:
            True if uninstalled, False if not found or is built-in.

        Raises:
            PluginInstallError: If attempting to uninstall a built-in plugin.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return False

        if record.is_builtin:
            raise PluginInstallError(
                f"Built-in plugin '{plugin_id}' cannot be uninstalled"
            )

        # Remove installation directory
        if record.install_path:
            path = Path(record.install_path)
            if await asyncio.to_thread(path.exists):
                await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)

        deleted = await self._store.delete_plugin(plugin_id)
        if deleted:
            self._invalidate_plugin(plugin_id)
        return deleted

    def validate_trigger_config(
        self,
        record: PluginRecord,
        config: dict[str, Any],
    ) -> None:
        """Validate complete trigger configuration before side effects or persistence."""

        capability = record.manifest.trigger
        if capability is None:
            self._require_capability(record, "trigger")
        assert capability is not None
        self._validate_config(record.plugin_id, "trigger", config, capability.config_schema)

    def validate_report_config(
        self,
        record: PluginRecord,
        config: dict[str, Any],
    ) -> None:
        """Validate complete report configuration before persistence."""

        capability = record.manifest.report
        if capability is None:
            self._require_capability(record, "report")
        assert capability is not None
        self._validate_config(record.plugin_id, "report", config, capability.config_schema)

    @staticmethod
    def _require_capability(record: PluginRecord, capability_name: str) -> None:
        if capability_name not in record.manifest.capabilities:
            raise PluginCapabilityError(
                f"Plugin '{record.plugin_id}' does not declare {capability_name} capability"
            )

    @staticmethod
    def _validate_config(
        plugin_id: str,
        capability_name: str,
        config: dict[str, Any],
        schema: dict[str, Any],
    ) -> None:
        strict_schema = {**schema}
        strict_schema.setdefault("type", "object")
        strict_schema.setdefault("additionalProperties", False)
        validator_type = validator_for(strict_schema)
        try:
            validator_type.check_schema(strict_schema)
        except SchemaError as error:
            raise PluginConfigurationError(
                f"Plugin '{plugin_id}' declares an invalid {capability_name} config schema"
            ) from error
        validator: Validator = validator_type(strict_schema)
        validation_error = next(validator.iter_errors(config), None)
        if validation_error is None:
            return
        field = (
            ".".join(str(part) for part in validation_error.absolute_path)
            or "configuration"
        )
        raise PluginConfigurationError(
            f"Plugin '{plugin_id}' {capability_name} config field '{field}' "
            f"violates schema rule '{validation_error.validator}'"
        )

    def _invalidate_plugin(self, plugin_id: str) -> None:
        if self._plugin_cache is not None:
            self._plugin_cache.invalidate(plugin_id)

    @staticmethod
    def _extract_defaults(schema: dict[str, Any]) -> dict[str, Any]:
        """Extract default values from a JSON schema.

        Args:
            schema: JSON schema with properties.

        Returns:
            Dictionary of property names to default values.
        """
        properties = schema.get("properties", {})
        defaults: dict[str, Any] = {}
        for key, prop in properties.items():
            if isinstance(prop, dict) and "default" in prop:
                defaults[key] = prop["default"]
        return defaults

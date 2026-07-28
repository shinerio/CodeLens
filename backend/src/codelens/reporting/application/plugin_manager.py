"""Manage report plugin lifecycle: install, enable, configure, uninstall."""

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path

from codelens.reporting.domain.models import PluginManifest, PluginRecord
from codelens.reporting.domain.ports import PluginStorePort
from codelens.reporting.infrastructure.git_installer import (
    GitPluginInstaller,
    PluginInstallError,
)


class PluginManager:
    """Orchestrate plugin installation, configuration, and removal.

    The manager owns the built-in ``local-file-export`` plugin and delegates
    external plugin installation to ``GitPluginInstaller``. All persistent
    state is written through ``PluginStorePort``.
    """

    BUILTIN_PLUGIN_ID = "local-file-export"

    def __init__(
        self,
        store: PluginStorePort,
        installer: GitPluginInstaller,
        plugins_dir: Path,
    ) -> None:
        self._store = store
        self._installer = installer
        self._plugins_dir = plugins_dir
        self._builtin_manifest = PluginManifest(
            plugin_id=self.BUILTIN_PLUGIN_ID,
            name="Local File Export",
            version="1.0.0",
            description=(
                "Export review findings to CodeLensReview directory "
                "in the reviewed repository"
            ),
            author="CodeLens Team",
            entry_point="local_file_sink:LocalFileExportSink",
            config_schema={
                "type": "object",
                "properties": {
                    "output_dir": {
                        "type": "string",
                        "default": "CodeLensReview",
                        "description": "Output directory name relative to reviewed repo root",
                    },
                    "formats": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["json", "markdown"]},
                        "default": ["json", "markdown"],
                    },
                },
            },
        )

    async def initialize_builtin(self) -> None:
        """Ensure the built-in plugin record exists in the store."""

        existing = await self._store.get_plugin(self.BUILTIN_PLUGIN_ID)
        if existing is not None:
            return
        record = PluginRecord(
            plugin_id=self.BUILTIN_PLUGIN_ID,
            manifest=self._builtin_manifest,
            is_enabled=True,
            is_builtin=True,
            install_path=None,
            config={},
            auto_export=False,
        )
        await self._store.save_plugin(record)

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return await self._store.list_plugins()

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        return await self._store.get_plugin(plugin_id)

    async def install_from_git(self, git_url: str, ref: str | None = None) -> PluginManifest:
        manifest = await self._installer.install(git_url, ref)
        install_path = str(self._plugins_dir / manifest.plugin_id)
        record = PluginRecord(
            plugin_id=manifest.plugin_id,
            manifest=manifest,
            is_enabled=False,
            is_builtin=False,
            install_path=install_path,
            config=self._extract_defaults(manifest),
            auto_export=False,
        )
        await self._store.save_plugin(record)
        return manifest

    async def enable_plugin(self, plugin_id: str) -> PluginRecord | None:
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None
        updated = replace(record, is_enabled=True)
        await self._store.save_plugin(updated)
        return updated

    async def disable_plugin(self, plugin_id: str) -> PluginRecord | None:
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None
        updated = replace(record, is_enabled=False)
        await self._store.save_plugin(updated)
        return updated

    async def update_config(self, plugin_id: str, config: dict) -> PluginRecord | None:
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None
        merged = {**record.config, **config}
        updated = replace(record, config=merged)
        await self._store.save_plugin(updated)
        return updated

    async def set_auto_export(self, plugin_id: str, enabled: bool) -> PluginRecord | None:
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return None
        updated = replace(record, auto_export=enabled)
        await self._store.save_plugin(updated)
        return updated

    async def uninstall_plugin(self, plugin_id: str) -> bool:
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            return False
        if record.is_builtin:
            raise PluginInstallError(
                f"Built-in plugin '{plugin_id}' cannot be uninstalled"
            )
        if record.install_path:
            path = Path(record.install_path)
            if await asyncio.to_thread(path.exists):
                await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
        return await self._store.delete_plugin(plugin_id)

    @staticmethod
    def _extract_defaults(manifest: PluginManifest) -> dict:
        schema = manifest.config_schema
        properties = schema.get("properties", {})
        defaults: dict = {}
        for key, prop in properties.items():
            if isinstance(prop, dict) and "default" in prop:
                defaults[key] = prop["default"]
        return defaults

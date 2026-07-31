"""Install plugins from remote Git repositories."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from codelens.plugin.domain.models import (
    PluginInstallError,
    PluginManifest,
    ReportCapability,
    TriggerCapability,
)
from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.infrastructure.git_cli import GitCli


class GitPluginInstaller:
    """Clone a Git repository, validate its manifest, and move it into place.

    Plugins are installed to ``{data_dir}/plugins/{plugin_id}/``. The
    installer performs a shallow clone to a temporary directory, reads and
    validates ``plugin.json``, then atomically moves the clone to its final
    path. A pre-existing directory for the same plugin_id is an error.
    """

    _MANIFEST_FILENAME = "plugin.json"
    _RESERVED_PLUGIN_IDS = frozenset({"local"})

    def __init__(self, git: GitCli, plugins_dir: Path) -> None:
        self._git = git
        self._plugins_dir = plugins_dir

    async def install(self, git_url: str, ref: str | None = None) -> PluginManifest:
        temp_dir = Path(tempfile.mkdtemp(prefix="codelens-plugin-install-"))
        try:
            try:
                await self._git.clone(git_url, temp_dir, ref=ref)
            except InvalidRepositoryError:
                raise PluginInstallError(
                    "Git repository could not be cloned"
                ) from None
            manifest = await asyncio.to_thread(self._read_manifest, temp_dir)
            self._validate_manifest(manifest)
            install_path = self._plugins_dir / manifest.plugin_id
            if install_path.exists():
                raise PluginInstallError(
                    f"plugin '{manifest.plugin_id}' is already installed"
                )
            install_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_dir), str(install_path))
            return manifest
        finally:
            if await asyncio.to_thread(temp_dir.exists):
                await asyncio.to_thread(shutil.rmtree, temp_dir, True)

    async def update(
        self,
        git_url: str,
        install_path: Path,
        ref: str | None = None,
    ) -> PluginManifest:
        """Update an installed plugin by cloning a new version and swapping directories.

        Args:
            git_url: Git repository URL.
            install_path: Current installation directory.
            ref: Optional Git reference (branch, tag, commit).

        Returns:
            Validated manifest of the new version.

        Raises:
            PluginInstallError: If update fails.
        """
        temp_dir = Path(tempfile.mkdtemp(prefix="codelens-plugin-update-"))
        backup_path = install_path.with_suffix(".bak")
        try:
            try:
                await self._git.clone(git_url, temp_dir, ref=ref)
            except InvalidRepositoryError:
                raise PluginInstallError(
                    "Git repository could not be cloned"
                ) from None
            manifest = await asyncio.to_thread(self._read_manifest, temp_dir)
            self._validate_manifest(manifest)

            # Atomic swap: old → .bak, new → install, remove .bak
            if backup_path.exists():
                await asyncio.to_thread(shutil.rmtree, backup_path, True)
            await asyncio.to_thread(install_path.rename, backup_path)
            try:
                shutil.move(str(temp_dir), str(install_path))
            except Exception:
                # Restore from backup if move fails
                if backup_path.exists():
                    backup_path.rename(install_path)
                raise
            await asyncio.to_thread(shutil.rmtree, backup_path, True)
            return manifest
        finally:
            if await asyncio.to_thread(temp_dir.exists):
                await asyncio.to_thread(shutil.rmtree, temp_dir, True)

    def _read_manifest(self, plugin_dir: Path) -> PluginManifest:
        manifest_path = plugin_dir / self._MANIFEST_FILENAME
        if not manifest_path.exists():
            raise PluginInstallError(
                f"plugin manifest '{self._MANIFEST_FILENAME}' not found in repository root"
            )
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise PluginInstallError("plugin manifest must be a JSON object")
        try:
            capabilities = self._parse_capabilities(raw.get("capabilities", {}))
            return PluginManifest(
                plugin_id=raw["plugin_id"],
                name=raw["name"],
                version=raw["version"],
                description=raw.get("description", ""),
                author=raw.get("author", ""),
                platform=raw.get("platform", "local"),
                capabilities=capabilities,
                min_codelens_version=raw.get("min_codelens_version"),
                name_i18n=raw.get("name_i18n", {}),
                description_i18n=raw.get("description_i18n", {}),
            )
        except KeyError as error:
            raise PluginInstallError(
                f"plugin manifest missing required field: {error}"
            ) from error

    def _parse_capabilities(self, raw_capabilities: dict) -> dict:
        """Parse capabilities dict into TriggerCapability/ReportCapability objects."""
        from typing import Any

        capabilities: dict[str, Any] = {}
        if "trigger" in raw_capabilities and raw_capabilities["trigger"]:
            trigger_raw = raw_capabilities["trigger"]
            capabilities["trigger"] = TriggerCapability(
                trigger_type=trigger_raw.get("trigger_type", "local-hook"),
                supported_events=tuple(trigger_raw.get("supported_events", [])),
                entry_point=trigger_raw["entry_point"],
                config_schema=trigger_raw.get("config_schema", {}),
            )
        if "report" in raw_capabilities and raw_capabilities["report"]:
            report_raw = raw_capabilities["report"]
            capabilities["report"] = ReportCapability(
                entry_point=report_raw["entry_point"],
                config_schema=report_raw.get("config_schema", {}),
            )
        return capabilities

    def _validate_manifest(self, manifest: PluginManifest) -> None:
        if not manifest.plugin_id or not manifest.plugin_id.replace("-", "_").isidentifier():
            raise PluginInstallError(
                f"plugin_id must be a valid identifier, got: {manifest.plugin_id}"
            )
        if manifest.plugin_id in self._RESERVED_PLUGIN_IDS:
            raise PluginInstallError(
                f"plugin_id '{manifest.plugin_id}' is reserved for a built-in plugin"
            )
        if not manifest.capabilities:
            raise PluginInstallError(
                "plugin must declare at least one capability (trigger or report)"
            )
        for cap_name, cap in manifest.capabilities.items():
            if cap_name not in ("trigger", "report"):
                raise PluginInstallError(f"unknown capability: {cap_name}")
            if not cap.entry_point or ":" not in cap.entry_point:
                raise PluginInstallError(
                    f"{cap_name}.entry_point must be 'module:Class', got: {cap.entry_point}"
                )
            try:
                validator_for(cap.config_schema).check_schema(cap.config_schema)
            except SchemaError as error:
                raise PluginInstallError(
                    f"{cap_name}.config_schema must be valid JSON Schema"
                ) from error

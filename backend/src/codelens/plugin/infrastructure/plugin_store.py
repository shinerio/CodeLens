"""Atomic local persistence for installed plugins.

Stores all plugins (built-in and external) in a single ``plugins.json`` file
under the data directory. Uses tempfile + fsync + os.replace for crash safety.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from codelens.plugin.domain.models import (
    PluginManifest,
    PluginRecord,
    ReportCapability,
    TriggerCapability,
)


class FilesystemPluginStore:
    """Persist plugin records as a single atomic JSON file.

    The store file lives at ``{data_dir}/plugins.json`` and uses the same
    tempfile + fsync + os.replace pattern as other CodeLens settings stores
    to guarantee crash-safe updates.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "plugins.json"
        self._data_dir = data_dir
        self._write_lock = asyncio.Lock()

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return await asyncio.to_thread(self._list_plugins_sync)

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        records = await asyncio.to_thread(self._list_plugins_sync)
        return next((r for r in records if r.plugin_id == plugin_id), None)

    async def save_plugin(self, record: PluginRecord) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self._save_plugin_sync, record)

    async def delete_plugin(self, plugin_id: str) -> bool:
        async with self._write_lock:
            return await asyncio.to_thread(self._delete_plugin_sync, plugin_id)

    def _list_plugins_sync(self) -> tuple[PluginRecord, ...]:
        payload = self._read()
        records: list[PluginRecord] = []
        for item in payload.get("plugins", []):
            if not isinstance(item, dict):
                continue
            record = _deserialize_record(item)
            if record is not None:
                records.append(record)
        return tuple(records)

    def _save_plugin_sync(self, record: PluginRecord) -> None:
        payload = self._read()
        plugins = payload.setdefault("plugins", [])
        for idx, existing in enumerate(plugins):
            if isinstance(existing, dict) and existing.get("plugin_id") == record.plugin_id:
                plugins[idx] = _serialize_record(record)
                break
        else:
            plugins.append(_serialize_record(record))
        self._write(payload)

    def _delete_plugin_sync(self, plugin_id: str) -> bool:
        payload = self._read()
        plugins = payload.get("plugins", [])
        before = len(plugins)
        payload["plugins"] = [
            p for p in plugins if isinstance(p, dict) and p.get("plugin_id") != plugin_id
        ]
        if len(payload["plugins"]) == before:
            return False
        self._write(payload)
        return True

    def _read(self) -> dict:
        if not self._path.exists():
            return {"plugins": []}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("plugin store is invalid")
        return raw

    def _write(self, payload: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".plugins-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(
                    payload, stream,
                    ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)


def _serialize_record(record: PluginRecord) -> dict:
    """Serialize a PluginRecord to a JSON-compatible dict."""
    manifest_dict: dict[str, Any] = {
        "plugin_id": record.manifest.plugin_id,
        "name": record.manifest.name,
        "version": record.manifest.version,
        "description": record.manifest.description,
        "author": record.manifest.author,
        "platform": record.manifest.platform,
        "capabilities": {},
        "min_codelens_version": record.manifest.min_codelens_version,
        "name_i18n": record.manifest.name_i18n,
        "description_i18n": record.manifest.description_i18n,
    }

    # Serialize capabilities
    capabilities_dict: dict[str, dict[str, Any]] = {}
    for key, cap in record.manifest.capabilities.items():
        if isinstance(cap, TriggerCapability):
            capabilities_dict[key] = {
                "type": "trigger",
                "trigger_type": cap.trigger_type,
                "supported_events": list(cap.supported_events),
                "entry_point": cap.entry_point,
                "config_schema": cap.config_schema,
            }
        elif isinstance(cap, ReportCapability):
            capabilities_dict[key] = {
                "type": "report",
                "entry_point": cap.entry_point,
                "config_schema": cap.config_schema,
            }
    manifest_dict["capabilities"] = capabilities_dict

    return {
        "plugin_id": record.plugin_id,
        "manifest": manifest_dict,
        "is_builtin": record.is_builtin,
        "install_path": record.install_path,
        "trigger_enabled": record.trigger_enabled,
        "report_enabled": record.report_enabled,
        "report_auto_export": record.report_auto_export,
        "trigger_config": record.trigger_config,
        "report_config": record.report_config,
        "git_url": record.git_url,
        "git_ref": record.git_ref,
    }


def _deserialize_record(data: dict) -> PluginRecord | None:
    """Deserialize a PluginRecord from a dict, returning None on failure."""
    try:
        manifest_data = data.get("manifest", {})

        # Deserialize capabilities
        capabilities: dict[str, TriggerCapability | ReportCapability] = {}
        for key, cap_data in manifest_data.get("capabilities", {}).items():
            cap_type = cap_data.get("type")
            if cap_type == "trigger":
                capabilities[key] = TriggerCapability(
                    trigger_type=cap_data["trigger_type"],
                    supported_events=tuple(cap_data["supported_events"]),
                    entry_point=cap_data["entry_point"],
                    config_schema=cap_data.get("config_schema", {}),
                )
            elif cap_type == "report":
                capabilities[key] = ReportCapability(
                    entry_point=cap_data["entry_point"],
                    config_schema=cap_data.get("config_schema", {}),
                )

        manifest = PluginManifest(
            plugin_id=manifest_data["plugin_id"],
            name=manifest_data["name"],
            version=manifest_data["version"],
            description=manifest_data.get("description", ""),
            author=manifest_data.get("author", ""),
            platform=manifest_data["platform"],
            capabilities=capabilities,
            min_codelens_version=manifest_data.get("min_codelens_version"),
            name_i18n=manifest_data.get("name_i18n", {}),
            description_i18n=manifest_data.get("description_i18n", {}),
        )

        return PluginRecord(
            plugin_id=data["plugin_id"],
            manifest=manifest,
            is_builtin=data.get("is_builtin", False),
            install_path=data.get("install_path"),
            trigger_enabled=data.get("trigger_enabled", False),
            report_enabled=data.get("report_enabled", False),
            report_auto_export=data.get("report_auto_export", False),
            trigger_config=data.get("trigger_config", {}),
            report_config=data.get("report_config", {}),
            git_url=data.get("git_url"),
            git_ref=data.get("git_ref"),
        )
    except (KeyError, TypeError, ValueError):
        return None

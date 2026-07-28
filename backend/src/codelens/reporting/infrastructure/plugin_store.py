"""Atomic local persistence for installed report plugins."""

import asyncio
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from codelens.reporting.domain.models import PluginManifest, PluginRecord


class FilesystemPluginStore:
    """Persist plugin records as a single atomic JSON file.

    The store file lives at ``{data_dir}/report-plugins.json`` and uses the
    same tempfile + fsync + os.replace pattern as other CodeLens settings
    stores to guarantee crash-safe updates.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "report-plugins.json"
        self._data_dir = data_dir

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return await asyncio.to_thread(self._list_plugins_sync)

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        records = await asyncio.to_thread(self._list_plugins_sync)
        return next((r for r in records if r.plugin_id == plugin_id), None)

    async def save_plugin(self, record: PluginRecord) -> None:
        await asyncio.to_thread(self._save_plugin_sync, record)

    async def delete_plugin(self, plugin_id: str) -> bool:
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
            raise ValueError("report plugin store is invalid")
        return raw

    def _write(self, payload: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".report-plugins-", suffix=".tmp"
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
    return {
        "plugin_id": record.plugin_id,
        "manifest": asdict(record.manifest),
        "is_enabled": record.is_enabled,
        "is_builtin": record.is_builtin,
        "install_path": record.install_path,
        "config": record.config,
        "auto_export": record.auto_export,
    }


def _deserialize_record(data: dict) -> PluginRecord | None:
    try:
        manifest_data = data.get("manifest", {})
        manifest = PluginManifest(
            plugin_id=manifest_data["plugin_id"],
            name=manifest_data["name"],
            version=manifest_data["version"],
            description=manifest_data.get("description", ""),
            author=manifest_data.get("author", ""),
            entry_point=manifest_data["entry_point"],
            config_schema=manifest_data.get("config_schema", {}),
            min_codelens_version=manifest_data.get("min_codelens_version"),
        )
        return PluginRecord(
            plugin_id=data["plugin_id"],
            manifest=manifest,
            is_enabled=data.get("is_enabled", False),
            is_builtin=data.get("is_builtin", False),
            install_path=data.get("install_path"),
            config=data.get("config", {}),
            auto_export=data.get("auto_export", False),
        )
    except (KeyError, TypeError):
        return None

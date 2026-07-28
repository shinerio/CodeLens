"""Atomic local persistence for installed trigger plugins."""

import asyncio
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
    TriggerManifest,
    TriggerRecord,
    TriggerType,
)


class FilesystemTriggerStore:
    """Persist trigger plugin records as a single atomic JSON file.

    The store file lives at ``{data_dir}/trigger-plugins.json`` and uses the
    same tempfile + fsync + os.replace pattern as other CodeLens settings
    stores to guarantee crash-safe updates.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "trigger-plugins.json"
        self._data_dir = data_dir

    async def list_triggers(self) -> tuple[TriggerRecord, ...]:
        return await asyncio.to_thread(self._list_triggers_sync)

    async def get_trigger(self, plugin_id: str) -> TriggerRecord | None:
        records = await asyncio.to_thread(self._list_triggers_sync)
        return next((r for r in records if r.plugin_id == plugin_id), None)

    async def save_trigger(self, record: TriggerRecord) -> None:
        await asyncio.to_thread(self._save_trigger_sync, record)

    async def delete_trigger(self, plugin_id: str) -> bool:
        return await asyncio.to_thread(self._delete_trigger_sync, plugin_id)

    def _list_triggers_sync(self) -> tuple[TriggerRecord, ...]:
        payload = self._read()
        records: list[TriggerRecord] = []
        for item in payload.get("triggers", []):
            if not isinstance(item, dict):
                continue
            record = _deserialize_record(item)
            if record is not None:
                records.append(record)
        return tuple(records)

    def _save_trigger_sync(self, record: TriggerRecord) -> None:
        payload = self._read()
        triggers = payload.setdefault("triggers", [])
        for idx, existing in enumerate(triggers):
            if isinstance(existing, dict) and existing.get("plugin_id") == record.plugin_id:
                triggers[idx] = _serialize_record(record)
                break
        else:
            triggers.append(_serialize_record(record))
        self._write(payload)

    def _delete_trigger_sync(self, plugin_id: str) -> bool:
        payload = self._read()
        triggers = payload.get("triggers", [])
        before = len(triggers)
        payload["triggers"] = [
            p for p in triggers if isinstance(p, dict) and p.get("plugin_id") != plugin_id
        ]
        if len(payload["triggers"]) == before:
            return False
        self._write(payload)
        return True

    def _read(self) -> dict:
        if not self._path.exists():
            return {"triggers": []}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("trigger plugin store is invalid")
        return raw

    def _write(self, payload: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".trigger-plugins-", suffix=".tmp"
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


def _serialize_record(record: TriggerRecord) -> dict:
    return {
        "plugin_id": record.plugin_id,
        "manifest": asdict(record.manifest),
        "is_enabled": record.is_enabled,
        "is_builtin": record.is_builtin,
        "install_path": record.install_path,
        "config": asdict(record.config),
    }


def _deserialize_record(data: dict) -> TriggerRecord | None:
    try:
        manifest_data = data.get("manifest", {})
        manifest = TriggerManifest(
            plugin_id=manifest_data["plugin_id"],
            name=manifest_data["name"],
            version=manifest_data["version"],
            description=manifest_data.get("description", ""),
            author=manifest_data.get("author", ""),
            entry_point=manifest_data["entry_point"],
            trigger_type=TriggerType(manifest_data["trigger_type"]),
            supported_events=tuple(
                HookEvent(e) for e in manifest_data["supported_events"]
            ),
            config_schema=manifest_data.get("config_schema", {}),
            min_codelens_version=manifest_data.get("min_codelens_version"),
        )
        config_data = data.get("config", {})
        config = TriggerConfig(
            repository_paths=tuple(config_data.get("repository_paths", [])),
            events=tuple(HookEvent(e) for e in config_data.get("events", [])),
            scope_type=config_data.get("scope_type", "commit"),
            base_ref=config_data.get("base_ref"),
            target_ref=config_data.get("target_ref"),
            selected_agents=tuple(config_data.get("selected_agents", [])),
            prompt_locale=config_data.get("prompt_locale", "en"),
            debounce_seconds=config_data.get("debounce_seconds", 10),
            extra=config_data.get("extra", {}),
        )
        return TriggerRecord(
            plugin_id=data["plugin_id"],
            manifest=manifest,
            is_enabled=data.get("is_enabled", False),
            is_builtin=data.get("is_builtin", False),
            install_path=data.get("install_path"),
            config=config,
        )
    except (KeyError, TypeError, ValueError):
        return None

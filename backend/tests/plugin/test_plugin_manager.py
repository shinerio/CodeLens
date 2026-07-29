from pathlib import Path
from typing import Any, cast

import pytest

from codelens.plugin.application.plugin_manager import PluginManager
from codelens.plugin.domain.models import (
    PluginCapabilityError,
    PluginConfigurationError,
    PluginManifest,
    PluginRecord,
    ReportCapability,
)
from codelens.plugin.domain.ports import PluginInstallerPort, PluginStorePort


class MemoryPluginStore:
    def __init__(self, record: PluginRecord) -> None:
        self.record = record

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return (self.record,)

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        return self.record if self.record.plugin_id == plugin_id else None

    async def save_plugin(self, record: PluginRecord) -> None:
        self.record = record

    async def delete_plugin(self, plugin_id: str) -> bool:
        return False


class EmptyPluginStore:
    def __init__(self) -> None:
        self.record: PluginRecord | None = None

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return (self.record,) if self.record is not None else ()

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        if self.record is not None and self.record.plugin_id == plugin_id:
            return self.record
        return None

    async def save_plugin(self, record: PluginRecord) -> None:
        self.record = record

    async def delete_plugin(self, plugin_id: str) -> bool:
        return False


def _record() -> PluginRecord:
    manifest = PluginManifest(
        plugin_id="report-only",
        name="Report only",
        version="1.0.0",
        description="",
        author="test",
        platform="local",
        capabilities={
            "report": ReportCapability(
                entry_point="sink:Sink",
                config_schema={
                    "type": "object",
                    "properties": {
                        "retries": {"type": "integer", "minimum": 0}
                    },
                },
            )
        },
    )
    return PluginRecord(
        plugin_id=manifest.plugin_id,
        manifest=manifest,
        is_builtin=True,
        install_path=None,
        trigger_enabled=False,
        report_enabled=False,
        report_auto_export=False,
        report_config={"retries": 1},
    )


def _manager(store: MemoryPluginStore) -> PluginManager:
    return PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, object()),
        Path("/unused"),
    )


async def test_capability_operations_reject_undeclared_capabilities() -> None:
    store = MemoryPluginStore(_record())

    with pytest.raises(PluginCapabilityError, match="trigger capability"):
        await _manager(store).enable_trigger("report-only")

    assert store.record.trigger_enabled is False


@pytest.mark.parametrize(
    "config",
    [
        {"retries": "3"},
        {"retries": -1},
        {"unsupported": True},
    ],
)
async def test_invalid_report_config_is_not_persisted(config: dict[str, Any]) -> None:
    store = MemoryPluginStore(_record())

    with pytest.raises(PluginConfigurationError):
        await _manager(store).update_report_config("report-only", config)

    assert store.record.report_config == {"retries": 1}


async def test_auto_export_requires_a_declared_report_capability() -> None:
    report_record = _record()
    trigger_only = PluginRecord(
        **{
            **report_record.__dict__,
            "plugin_id": "trigger-only",
            "manifest": PluginManifest(
                **{
                    **report_record.manifest.__dict__,
                    "plugin_id": "trigger-only",
                    "capabilities": {},
                }
            ),
        }
    )
    store = MemoryPluginStore(trigger_only)

    with pytest.raises(PluginCapabilityError, match="report capability"):
        await _manager(store).set_auto_export("trigger-only", True)

    assert store.record.report_auto_export is False


async def test_builtin_trigger_rejects_an_empty_agent_selection() -> None:
    store = EmptyPluginStore()
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, object()),
        Path("/unused"),
    )
    await manager.initialize_builtin()

    with pytest.raises(PluginConfigurationError):
        await manager.update_trigger_config("local", {"selected_agents": []})

    assert store.record is not None
    assert store.record.trigger_config["selected_agents"] == ["correctness:v1"]

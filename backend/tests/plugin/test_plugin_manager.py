from pathlib import Path
from typing import Any, cast

import pytest

from codelens.plugin.application.plugin_manager import PluginManager
from codelens.plugin.domain.models import (
    PluginCapabilityError,
    PluginConfigurationError,
    PluginInstallError,
    PluginManifest,
    PluginRecord,
    ReportCapability,
    TriggerCapability,
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


class MockInstaller:
    """Mock installer that returns a new manifest for update testing."""

    def __init__(self, new_manifest: PluginManifest) -> None:
        self.new_manifest = new_manifest

    async def install(self, git_url: str, ref: str | None = None) -> PluginManifest:
        return self.new_manifest

    async def update(
        self, git_url: str, install_path: Path, ref: str | None = None
    ) -> PluginManifest:
        return self.new_manifest


def _external_record() -> PluginRecord:
    """Create an external plugin record with git_url and config."""
    manifest = PluginManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="1.0.0",
        description="Test",
        author="test",
        platform="github",
        capabilities={
            "trigger": TriggerCapability(
                trigger_type="webhook",
                supported_events=("webhook",),
                entry_point="trigger:Trigger",
                config_schema={
                    "type": "object",
                    "properties": {
                        "api_key": {"type": "string"},
                        "timeout": {"type": "integer", "default": 30},
                    },
                },
            ),
            "report": ReportCapability(
                entry_point="sink:Sink",
                config_schema={
                    "type": "object",
                    "properties": {
                        "output_format": {"type": "string", "default": "json"},
                    },
                },
            ),
        },
    )
    return PluginRecord(
        plugin_id=manifest.plugin_id,
        manifest=manifest,
        is_builtin=False,
        install_path="/data/plugins/test-plugin",
        trigger_enabled=True,
        report_enabled=True,
        report_auto_export=True,
        trigger_config={"api_key": "secret123", "timeout": 60},
        report_config={"output_format": "markdown"},
        git_url="https://github.com/test/plugin.git",
        git_ref="v1.0.0",
    )


async def test_update_plugin_preserves_existing_config() -> None:
    """Update should keep existing config values for fields still in schema."""
    record = _external_record()
    store = MemoryPluginStore(record)

    # New manifest with same fields
    new_manifest = PluginManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="2.0.0",
        description="Test v2",
        author="test",
        platform="github",
        capabilities={
            "trigger": TriggerCapability(
                trigger_type="webhook",
                supported_events=("webhook",),
                entry_point="trigger:Trigger",
                config_schema={
                    "type": "object",
                    "properties": {
                        "api_key": {"type": "string"},
                        "timeout": {"type": "integer", "default": 30},
                    },
                },
            ),
            "report": ReportCapability(
                entry_point="sink:Sink",
                config_schema={
                    "type": "object",
                    "properties": {
                        "output_format": {"type": "string", "default": "json"},
                    },
                },
            ),
        },
    )

    installer = MockInstaller(new_manifest)
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, installer),
        Path("/data/plugins"),
    )

    updated = await manager.update_plugin("test-plugin")

    # Config should be preserved
    assert updated.trigger_config == {"api_key": "secret123", "timeout": 60}
    assert updated.report_config == {"output_format": "markdown"}
    # Enabled states should be preserved
    assert updated.trigger_enabled is True
    assert updated.report_enabled is True
    assert updated.report_auto_export is True
    # Version should be updated
    assert updated.manifest.version == "2.0.0"


async def test_update_plugin_adds_defaults_for_new_fields() -> None:
    """Update should add default values for newly introduced config fields."""
    record = _external_record()
    store = MemoryPluginStore(record)

    # New manifest with additional field
    new_manifest = PluginManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="2.0.0",
        description="Test v2",
        author="test",
        platform="github",
        capabilities={
            "trigger": TriggerCapability(
                trigger_type="webhook",
                supported_events=("webhook",),
                entry_point="trigger:Trigger",
                config_schema={
                    "type": "object",
                    "properties": {
                        "api_key": {"type": "string"},
                        "timeout": {"type": "integer", "default": 30},
                        "retry_count": {"type": "integer", "default": 3},  # New field
                    },
                },
            ),
            "report": ReportCapability(
                entry_point="sink:Sink",
                config_schema={
                    "type": "object",
                    "properties": {
                        "output_format": {"type": "string", "default": "json"},
                    },
                },
            ),
        },
    )

    installer = MockInstaller(new_manifest)
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, installer),
        Path("/data/plugins"),
    )

    updated = await manager.update_plugin("test-plugin")

    # Existing config should be preserved, new field should get default
    assert updated.trigger_config == {
        "api_key": "secret123",
        "timeout": 60,
        "retry_count": 3,
    }


async def test_update_plugin_drops_removed_fields() -> None:
    """Update should drop config fields that are no longer in the schema."""
    record = _external_record()
    store = MemoryPluginStore(record)

    # New manifest without 'timeout' field
    new_manifest = PluginManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="2.0.0",
        description="Test v2",
        author="test",
        platform="github",
        capabilities={
            "trigger": TriggerCapability(
                trigger_type="webhook",
                supported_events=("webhook",),
                entry_point="trigger:Trigger",
                config_schema={
                    "type": "object",
                    "properties": {
                        "api_key": {"type": "string"},
                        # 'timeout' removed
                    },
                },
            ),
            "report": ReportCapability(
                entry_point="sink:Sink",
                config_schema={
                    "type": "object",
                    "properties": {
                        "output_format": {"type": "string", "default": "json"},
                    },
                },
            ),
        },
    )

    installer = MockInstaller(new_manifest)
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, installer),
        Path("/data/plugins"),
    )

    updated = await manager.update_plugin("test-plugin")

    # 'timeout' should be dropped
    assert updated.trigger_config == {"api_key": "secret123"}


async def test_update_plugin_rejects_builtin() -> None:
    """Update should reject built-in plugins."""
    record = _record()  # Built-in plugin
    store = MemoryPluginStore(record)
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, object()),
        Path("/unused"),
    )

    with pytest.raises(PluginInstallError, match="Built-in plugin"):
        await manager.update_plugin("report-only")


async def test_update_plugin_rejects_missing_git_url() -> None:
    """Update should reject plugins without git_url."""
    record = _external_record()
    # Remove git_url
    record = PluginRecord(
        plugin_id=record.plugin_id,
        manifest=record.manifest,
        is_builtin=record.is_builtin,
        install_path=record.install_path,
        trigger_enabled=record.trigger_enabled,
        report_enabled=record.report_enabled,
        report_auto_export=record.report_auto_export,
        trigger_config=record.trigger_config,
        report_config=record.report_config,
        git_url=None,  # No git_url
        git_ref=None,
    )
    store = MemoryPluginStore(record)
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, object()),
        Path("/data/plugins"),
    )

    with pytest.raises(PluginInstallError, match="no Git source URL"):
        await manager.update_plugin("test-plugin")

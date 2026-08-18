import asyncio
import json
from dataclasses import replace
from pathlib import Path

from codelens.plugin.domain.models import (
    ManualReviewCapability,
    PluginManifest,
    PluginRecord,
)
from codelens.plugin.infrastructure.plugin_store import FilesystemPluginStore


def _record(plugin_id: str) -> PluginRecord:
    return PluginRecord(
        plugin_id=plugin_id,
        manifest=PluginManifest(
            plugin_id=plugin_id,
            name=plugin_id,
            version="1.0.0",
            description="",
            author="test",
            platform="local",
        ),
        is_builtin=False,
        install_path=f"/plugins/{plugin_id}",
        trigger_enabled=False,
        report_enabled=False,
        report_auto_export=False,
    )


def _manual_review_record(plugin_id: str = "codehub") -> PluginRecord:
    """Build a record with manual_review capability and config."""
    return replace(
        _record(plugin_id),
        manifest=PluginManifest(
            plugin_id=plugin_id,
            name=plugin_id,
            version="2.1.0",
            description="",
            author="test",
            platform="codehub",
            capabilities={
                "manual_review": ManualReviewCapability(
                    entry_point="codehub_trigger:CodehubTrigger",
                    config_schema={"type": "object"},
                )
            },
        ),
        manual_review_enabled=True,
        manual_review_config={"codehub_host": "example.com"},
    )


async def test_concurrent_updates_do_not_overwrite_other_plugin_records(
    tmp_path: Path,
) -> None:
    store = FilesystemPluginStore(tmp_path)
    records = [_record(f"plugin-{index}") for index in range(40)]

    await asyncio.gather(*(store.save_plugin(record) for record in records))
    await asyncio.gather(
        *(store.save_plugin(replace(record, report_enabled=True)) for record in records)
    )

    persisted = await store.list_plugins()
    assert {record.plugin_id for record in persisted} == {record.plugin_id for record in records}
    assert all(record.report_enabled for record in persisted)


async def test_manual_review_fields_round_trip(tmp_path: Path) -> None:
    """manual_review_enabled and manual_review_config survive save/load."""
    store = FilesystemPluginStore(tmp_path)
    record = _manual_review_record()

    await store.save_plugin(record)
    loaded = await store.get_plugin(record.plugin_id)

    assert loaded is not None
    assert loaded.manual_review_enabled is True
    assert loaded.manual_review_config == {"codehub_host": "example.com"}
    assert loaded.manifest.manual_review is not None
    assert loaded.manifest.manual_review.entry_point == "codehub_trigger:CodehubTrigger"


async def test_old_record_without_manual_review_fields_loads_with_defaults(
    tmp_path: Path,
) -> None:
    """A plugins.json without manual_review fields deserializes with defaults."""
    store = FilesystemPluginStore(tmp_path)
    # Simulate an old-format record written before manual_review existed.
    old_data = {
        "plugins": [
            {
                "plugin_id": "legacy",
                "manifest": {
                    "plugin_id": "legacy",
                    "name": "Legacy",
                    "version": "1.0.0",
                    "description": "",
                    "author": "test",
                    "platform": "local",
                    "capabilities": {},
                    "plugin_api_version": "2",
                },
                "is_builtin": False,
                "install_path": "/plugins/legacy",
                "trigger_enabled": False,
                "report_enabled": False,
                "report_auto_export": False,
                "trigger_config": {},
                "report_config": {},
            }
        ]
    }
    # Write to the path the store reads from.
    store_path = tmp_path / "plugins.json"
    store_path.write_text(json.dumps(old_data), encoding="utf-8")

    loaded = await store.get_plugin("legacy")
    assert loaded is not None
    assert loaded.manual_review_enabled is False
    assert loaded.manual_review_config == {}

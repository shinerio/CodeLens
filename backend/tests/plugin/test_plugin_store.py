import asyncio
from dataclasses import replace
from pathlib import Path

from codelens.plugin.domain.models import PluginManifest, PluginRecord
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

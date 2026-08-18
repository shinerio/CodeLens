from typing import cast

from codelens.plugin.application.export_orchestrator import (
    ExportOrchestrator,
    ReviewExportStorePort,
    RevisionReaderPort,
)
from codelens.plugin.domain.models import PluginManifest, PluginRecord
from codelens.plugin.domain.ports import PluginLoaderPort, PluginStorePort
from codelens.plugin.report.local_file_export.sink import LocalFileExportSink

from .test_local_file_export import _export_envelope


class MissingPluginStore:
    async def get_plugin(self, plugin_id: str) -> None:
        return None


async def test_missing_plugin_export_has_a_serializable_timestamp() -> None:
    orchestrator = ExportOrchestrator(
        cast(ReviewExportStorePort, object()),
        cast(RevisionReaderPort, object()),
        cast(PluginStorePort, MissingPluginStore()),
        cast(PluginLoaderPort, object()),
    )

    result = await orchestrator.export_findings("review-1", "missing")

    assert result.success is False
    assert result.exported_at is not None
    assert result.exported_at.tzinfo is not None


def test_builtin_report_sink_uses_the_unified_plugin_id() -> None:
    assert LocalFileExportSink().sink_id == "local"


class FailingExecutionStore:
    async def get_execution(self, task_id: str) -> None:
        del task_id
        raise RuntimeError("database unavailable")


async def test_execution_lookup_failure_is_not_reported_as_a_plugin_load_failure() -> None:
    orchestrator = ExportOrchestrator(
        cast(ReviewExportStorePort, FailingExecutionStore()),
        cast(RevisionReaderPort, object()),
        cast(PluginStorePort, object()),
        cast(PluginLoaderPort, object()),
    )
    plugin_record = PluginRecord(
        plugin_id="local",
        manifest=PluginManifest(
            plugin_id="local",
            name="Local",
            version="2.0.0",
            description="",
            author="test",
            platform="local",
        ),
        is_builtin=True,
        install_path=None,
        trigger_enabled=False,
        report_enabled=True,
        report_auto_export=False,
    )

    result = await orchestrator._export_to_plugin(plugin_record, _export_envelope())

    assert result.success is False
    assert result.error == "Review execution record could not be loaded"

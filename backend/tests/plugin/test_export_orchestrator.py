from typing import cast

from codelens.plugin.application.export_orchestrator import (
    ExportOrchestrator,
    ReviewExportStorePort,
    RevisionReaderPort,
)
from codelens.plugin.domain.ports import PluginLoaderPort, PluginStorePort
from codelens.plugin.report.local_file_export.sink import LocalFileExportSink


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

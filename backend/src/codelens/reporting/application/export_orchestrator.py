"""Orchestrate finding exports: manual triggers and automatic post-completion."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from codelens.reporting.domain.models import ExportResult, PluginRecord
from codelens.reporting.domain.ports import (
    PluginLoaderPort,
    PluginStorePort,
    ReportSinkPort,
    ReviewExportStorePort,
    RevisionReaderPort,
)
from codelens.reporting.infrastructure.local_file_sink import LocalFileExportSink
from codelens.review.application.export_findings import (
    ExportFindingsHandler,
)

_LOGGER = logging.getLogger("codelens.reporting")


class ExportOrchestrator:
    """Build export envelopes and deliver them through report sinks.

    Manual exports are triggered per-plugin by the user. Automatic exports
    fire after a review reaches a terminal status; all enabled auto-export
    plugins execute sequentially, and a single plugin failure is captured
    without blocking the remaining sinks.
    """

    def __init__(
        self,
        store: ReviewExportStorePort,
        reader: RevisionReaderPort,
        plugin_store: PluginStorePort,
        loader: PluginLoaderPort,
    ) -> None:
        self._store = store
        self._reader = reader
        self._plugin_store = plugin_store
        self._loader = loader
        self._builtin_sink = LocalFileExportSink()
        self._export_handler = ExportFindingsHandler(store, reader)

    async def export_manual(self, task_id: str, plugin_id: str) -> ExportResult:
        """Trigger one plugin export for one review task."""

        record = await self._plugin_store.get_plugin(plugin_id)
        if record is None:
            return self._failure_result(
                plugin_id, task_id, f"Plugin '{plugin_id}' is not installed"
            )
        if not record.is_enabled:
            return self._failure_result(
                plugin_id, task_id, f"Plugin '{plugin_id}' is not enabled"
            )
        return await self._run_export(task_id, record)

    async def auto_export_if_enabled(self, task_id: str) -> tuple[ExportResult, ...]:
        """Run all enabled auto-export plugins sequentially with failure isolation.

        This is the completion hook called after a review reaches a terminal
        status. Each plugin's failure is logged and recorded as a failed
        ``ExportResult``; the loop continues to the next plugin.
        """

        records = await self._plugin_store.list_plugins()
        auto_records = [r for r in records if r.is_enabled and r.auto_export]
        if not auto_records:
            return ()
        results: list[ExportResult] = []
        for record in auto_records:
            try:
                result = await self._run_export(task_id, record)
            except Exception as error:
                _LOGGER.error(
                    "Auto-export plugin '%s' failed for task '%s': %s",
                    record.plugin_id,
                    task_id,
                    error,
                    extra={"plugin_id": record.plugin_id, "task_id": task_id},
                )
                result = self._failure_result(record.plugin_id, task_id, str(error))
            results.append(result)
        return tuple(results)

    async def _run_export(self, task_id: str, record: PluginRecord) -> ExportResult:
        """Build the envelope, load the sink, and deliver the export."""

        try:
            envelope = await self._export_handler.build_envelope(task_id)
        except KeyError:
            return self._failure_result(record.plugin_id, task_id, f"Review '{task_id}' not found")
        except ValueError as error:
            return self._failure_result(record.plugin_id, task_id, str(error))

        execution = await self._store.get_execution(task_id)
        if execution is None:
            return self._failure_result(record.plugin_id, task_id, "Execution record not found")

        sink = self._resolve_sink(record)
        if sink is None:
            return self._failure_result(
                record.plugin_id, task_id,
                f"Sink for plugin '{record.plugin_id}' could not be loaded",
            )

        try:
            return await sink.export(
                envelope=envelope,
                config=record.config,
                repository_path=execution.repository_path,
            )
        except Exception as error:
            _LOGGER.error(
                "Sink '%s' export failed for task '%s': %s",
                record.plugin_id,
                task_id,
                error,
                extra={"plugin_id": record.plugin_id, "task_id": task_id},
            )
            return self._failure_result(record.plugin_id, task_id, str(error))

    def _resolve_sink(self, record: PluginRecord) -> ReportSinkPort | None:
        """Return the sink instance for a plugin record."""

        if record.is_builtin:
            return self._builtin_sink
        if record.install_path is None:
            return None
        try:
            return self._loader.load_sink(record.manifest, Path(record.install_path))
        except Exception as error:
            _LOGGER.error(
                "Failed to load plugin sink '%s': %s",
                record.plugin_id,
                error,
                extra={"plugin_id": record.plugin_id},
            )
            return None

    @staticmethod
    def _failure_result(plugin_id: str, task_id: str, error: str) -> ExportResult:
        return ExportResult(
            plugin_id=plugin_id,
            task_id=task_id,
            success=False,
            output_path=None,
            error=error,
            exported_at=datetime.now(UTC),
        )

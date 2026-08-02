"""Orchestrate finding export to report plugins."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from codelens.findings.domain.models import Finding
from codelens.plugin.domain.models import ExportHistoryEntry, ExportResult, PluginRecord
from codelens.plugin.domain.ports import (
    ExportHistoryPort,
    PluginLoaderPort,
    PluginStorePort,
    ReportSinkPort,
)
from codelens.plugin.domain.versioning import PluginApiVersion
from codelens.plugin.report.local_file_export.sink import LocalFileExportSink
from codelens.review.application.export_findings import (
    ExportFindingsHandler,
    FindingExportEnvelope,
    FindingExportEnvelopeV1,
    to_v1_export_envelope,
)
from codelens.review.domain.ports import ReviewExecutionRecord, ReviewPlanRecord, ReviewRecord


class ReviewPlanStorePort(Protocol):
    async def get(self, task_id: str) -> ReviewPlanRecord | None: ...


_LOGGER = logging.getLogger("codelens.plugin.report.orchestrator")


class ReviewExportStorePort(Protocol):
    """Port for accessing review data needed for export.

    This is a composite port that combines review record access with
    execution record and finding data access.
    """

    async def get_review(self, task_id: str) -> ReviewRecord | None:
        """Get a review record by task ID."""
        ...

    async def get_execution(self, task_id: str) -> ReviewExecutionRecord | None:
        """Get a review execution record by task ID."""
        ...

    async def list_findings(self, task_id: str) -> Sequence[Finding]:
        """List all findings for a review task."""
        raise NotImplementedError


class RevisionReaderPort(Protocol):
    """Port for reading file revisions from git repositories."""

    async def read_revision_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
    ) -> bytes | None:
        """Read a file at a specific revision, returning None if not found."""
        ...


class ExportOrchestrator:
    """Orchestrate finding export to report plugins.

    Responsibilities:
    - Build export envelopes from review findings
    - Route exports to enabled report plugins
    - Apply platform-based routing for auto-exports
    - Handle manual exports to specific plugins
    - Aggregate results and handle failures gracefully
    """

    def __init__(
        self,
        review_store: ReviewExportStorePort,
        revision_reader: RevisionReaderPort,
        plugin_store: PluginStorePort,
        plugin_loader: PluginLoaderPort,
        export_history: ExportHistoryPort | None = None,
        review_plan_store: ReviewPlanStorePort | None = None,
        checkpoint_store: Any | None = None,
    ) -> None:
        """Initialize the export orchestrator.

        Args:
            review_store: Port for accessing review data.
            revision_reader: Port for reading file revisions.
            plugin_store: Port for querying plugin state.
            plugin_loader: Port for loading plugin implementations.
            export_history: Optional port for persisting export results.
        """
        self._review_store = review_store
        self._revision_reader = revision_reader
        self._plugin_store = plugin_store
        self._plugin_loader = plugin_loader
        self._export_history = export_history
        self._envelope_builder = ExportFindingsHandler(
            review_store,
            revision_reader,
            review_plan_store,
            checkpoint_store,
        )
        self._builtin_sink = LocalFileExportSink()

    async def export_findings(
        self,
        task_id: str,
        plugin_id: str,
    ) -> ExportResult:
        """Manually export findings to a specific plugin.

        Args:
            task_id: The review task ID.
            plugin_id: The target plugin ID.

        Returns:
            Export result indicating success or failure.
        """
        # Find the plugin
        plugin_record = await self._plugin_store.get_plugin(plugin_id)
        if plugin_record is None:
            result = ExportResult(
                plugin_id=plugin_id,
                task_id=task_id,
                success=False,
                output_path=None,
                error=f"Plugin '{plugin_id}' not found",
                exported_at=datetime.now(UTC),
            )
            await self._save_history(result)
            return result

        if not plugin_record.report_enabled:
            result = ExportResult(
                plugin_id=plugin_id,
                task_id=task_id,
                success=False,
                output_path=None,
                error=f"Plugin '{plugin_id}' report capability is not enabled",
                exported_at=datetime.now(UTC),
            )
            await self._save_history(result)
            return result

        # Build the export envelope
        try:
            envelope = await self._envelope_builder.build_envelope(task_id)
        except Exception as error:
            _LOGGER.exception("Failed to build export envelope for task %s", task_id)
            result = ExportResult(
                plugin_id=plugin_id,
                task_id=task_id,
                success=False,
                output_path=None,
                error=f"Failed to build export envelope: {error}",
                exported_at=datetime.now(UTC),
            )
            await self._save_history(result)
            return result

        # Export to the plugin
        result = await self._export_to_plugin(plugin_record, envelope)
        await self._save_history(result)
        return result

    async def auto_export_if_enabled(
        self,
        task_id: str,
    ) -> tuple[ExportResult, ...]:
        """Automatically export findings to all enabled auto-export plugins.

        Applies platform-based routing: only exports to plugins whose platform
        matches the review's external_context platform (or "local" if no context).

        Args:
            task_id: The review task ID.

        Returns:
            Tuple of export results for each plugin.
        """
        # Query all plugins with enabled report capability and auto-export
        all_plugins = await self._plugin_store.list_plugins()
        auto_export_plugins = [
            p for p in all_plugins
            if p.report_enabled and p.report_auto_export
        ]

        if not auto_export_plugins:
            _LOGGER.debug("No auto-export plugins enabled")
            return ()

        # Build the export envelope once
        try:
            envelope = await self._envelope_builder.build_envelope(task_id)
        except Exception as error:
            _LOGGER.exception("Failed to build export envelope for task %s", task_id)
            result = ExportResult(
                plugin_id="all",
                task_id=task_id,
                success=False,
                output_path=None,
                error=f"Failed to build export envelope: {error}",
                exported_at=datetime.now(UTC),
            )
            await self._save_history(result)
            return (result,)

        # Determine the review's platform from external_context
        review_platform = "local"
        if envelope.review.external_context:
            review_platform = envelope.review.external_context.get("platform", "local")

        _LOGGER.debug(
            "Review platform: %s, checking %d auto-export plugins",
            review_platform,
            len(auto_export_plugins),
        )

        # Export to each matching plugin
        results: list[ExportResult] = []
        for plugin_record in auto_export_plugins:
            # Platform-based routing: only export if plugin platform matches review platform
            if plugin_record.manifest.platform != review_platform:
                _LOGGER.debug(
                    "Skipping plugin %s (platform %s != review platform %s)",
                    plugin_record.plugin_id,
                    plugin_record.manifest.platform,
                    review_platform,
                )
                continue

            try:
                result = await self._export_to_plugin(plugin_record, envelope)
                await self._save_history(result)
                results.append(result)
            except Exception:
                _LOGGER.exception(
                    "Plugin %s failed to export findings",
                    plugin_record.plugin_id,
                )
                result = ExportResult(
                    plugin_id=plugin_record.plugin_id,
                    task_id=task_id,
                    success=False,
                    output_path=None,
                    error="Plugin export failed with exception",
                    exported_at=datetime.now(UTC),
                )
                await self._save_history(result)
                results.append(result)

        return tuple(results)

    async def _export_to_plugin(
        self,
        plugin_record: PluginRecord,
        envelope: FindingExportEnvelope,
    ) -> ExportResult:
        """Export findings to a single plugin.

        Args:
            plugin_record: The plugin record.
            envelope: The export envelope.

        Returns:
            Export result.
        """
        try:
            sink = self._load_sink(plugin_record)
            execution = await self._review_store.get_execution(envelope.review.task_id)
        except Exception:
            _LOGGER.exception("Plugin %s could not be loaded", plugin_record.plugin_id)
            return ExportResult(
                plugin_id=plugin_record.plugin_id,
                task_id=envelope.review.task_id,
                success=False,
                output_path=None,
                error="Plugin could not be loaded",
                exported_at=datetime.now(UTC),
            )

        if execution is None:
            return ExportResult(
                plugin_id=plugin_record.plugin_id,
                task_id=envelope.review.task_id,
                success=False,
                output_path=None,
                error="Review execution record not found",
                exported_at=datetime.now(UTC),
            )

        try:
            sink_envelope: FindingExportEnvelope | FindingExportEnvelopeV1 = envelope
            if plugin_record.manifest.plugin_api_version is PluginApiVersion.V1:
                sink_envelope = to_v1_export_envelope(envelope)
            raw_result = await sink.export(
                envelope=sink_envelope,
                config=plugin_record.report_config,
                repository_path=execution.repository_path,
            )
        except Exception:
            _LOGGER.exception("Plugin %s failed to export findings", plugin_record.plugin_id)
            return ExportResult(
                plugin_id=plugin_record.plugin_id,
                task_id=envelope.review.task_id,
                success=False,
                output_path=None,
                error="Plugin export failed with exception",
                exported_at=datetime.now(UTC),
            )

        # External plugins may return a plain dict instead of ExportResult.
        if isinstance(raw_result, ExportResult):
            return raw_result
        if isinstance(raw_result, dict):
            return ExportResult(
                plugin_id=raw_result.get("plugin_id", plugin_record.plugin_id),
                task_id=raw_result.get("task_id", envelope.review.task_id),
                success=bool(raw_result.get("success", False)),
                output_path=raw_result.get("output_path"),
                error=raw_result.get("error"),
                exported_at=datetime.fromisoformat(raw_result["exported_at"])
                if "exported_at" in raw_result
                else datetime.now(UTC),
            )
        _LOGGER.error(
            "Plugin %s returned unexpected type: %s",
            plugin_record.plugin_id,
            type(raw_result),
        )
        return ExportResult(
            plugin_id=plugin_record.plugin_id,
            task_id=envelope.review.task_id,
            success=False,
            output_path=None,
            error=f"Plugin returned unexpected result type: {type(raw_result).__name__}",
            exported_at=datetime.now(UTC),
        )

    async def _save_history(self, result: ExportResult) -> None:
        """Persist an export result to history if the port is available."""
        if self._export_history is None:
            return
        try:
            await self._export_history.save(
                ExportHistoryEntry(
                    plugin_id=result.plugin_id,
                    task_id=result.task_id,
                    success=result.success,
                    output_path=result.output_path,
                    error=result.error,
                    exported_at=result.exported_at,
                )
            )
        except Exception:
            _LOGGER.exception("Failed to save export history")

    def _load_sink(self, plugin_record: PluginRecord) -> ReportSinkPort:
        """Load and instantiate a report plugin implementation.

        For built-in plugins, returns the built-in sink.
        For external plugins, delegates to the plugin_loader.

        Args:
            plugin_record: The plugin record to load.

        Returns:
            Instantiated report plugin implementing ReportSinkPort.

        Raises:
            ValueError: If the plugin type is not supported.
        """
        if plugin_record.is_builtin:
            return self._builtin_sink

        if not plugin_record.install_path:
            raise ValueError(
                f"External plugin '{plugin_record.plugin_id}' has no install_path"
            )

        return self._plugin_loader.load_sink(
            plugin_record.manifest,
            Path(plugin_record.install_path),
        )

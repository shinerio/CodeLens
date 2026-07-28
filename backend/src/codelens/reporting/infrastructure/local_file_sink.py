"""Built-in report sink that writes findings to the reviewed repository."""

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from codelens.findings.infrastructure.export_formatters import (
    JsonFindingExportFormatter,
    MarkdownFindingExportFormatter,
)
from codelens.reporting.domain.models import ExportResult
from codelens.reporting.domain.ports import ReportSinkPort
from codelens.review.application.export_findings import (
    FindingExportEnvelope,
    FindingExportFormatterPort,
)


class LocalFileExportSink(ReportSinkPort):
    """Write JSON and Markdown exports to ``{repo}/CodeLensReview/``.

    This is the built-in, always-available sink. It cannot be uninstalled.
    The output directory name and formats are configurable via the plugin
    config; defaults are ``CodeLensReview`` and ``["json", "markdown"]``.
    """

    _DEFAULT_OUTPUT_DIR = "CodeLensReview"
    _DEFAULT_FORMATS = ("json", "markdown")

    def __init__(self) -> None:
        self._formatters: dict[str, FindingExportFormatterPort] = {
            "json": JsonFindingExportFormatter(),
            "markdown": MarkdownFindingExportFormatter(),
        }

    @property
    def sink_id(self) -> str:
        return "local-file-export"

    @property
    def display_name(self) -> str:
        return "Local File Export"

    async def export(
        self,
        envelope: FindingExportEnvelope,
        config: dict,
        repository_path: Path,
    ) -> ExportResult:
        output_dir_name = config.get("output_dir", self._DEFAULT_OUTPUT_DIR)
        formats = tuple(config.get("formats", list(self._DEFAULT_FORMATS)))
        target_dir = repository_path / output_dir_name

        try:
            await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
        except OSError as error:
            return ExportResult(
                plugin_id=self.sink_id,
                task_id=envelope.review.task_id,
                success=False,
                output_path=None,
                error=f"Failed to create output directory: {error}",
                exported_at=datetime.now(UTC),
            )

        written_files: list[str] = []
        for fmt_id in formats:
            formatter = self._formatters.get(fmt_id)
            if formatter is None:
                continue
            content = formatter.format(envelope)
            filename = f"findings.{formatter.file_extension}"
            file_path = target_dir / filename
            try:
                await asyncio.to_thread(self._atomic_write, file_path, content)
                written_files.append(str(file_path))
            except OSError as error:
                return ExportResult(
                    plugin_id=self.sink_id,
                    task_id=envelope.review.task_id,
                    success=False,
                    output_path=None,
                    error=f"Failed to write {filename}: {error}",
                    exported_at=datetime.now(UTC),
                )

        return ExportResult(
            plugin_id=self.sink_id,
            task_id=envelope.review.task_id,
            success=True,
            output_path=str(target_dir),
            error=None,
            exported_at=datetime.now(UTC),
        )

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        """Write content to path atomically using tempfile + os.replace."""

        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

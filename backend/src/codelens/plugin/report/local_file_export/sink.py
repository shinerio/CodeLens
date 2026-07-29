"""Built-in report sink that writes findings to the reviewed repository."""

import asyncio
import os
import stat
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from pathspec import PathSpec

from codelens.findings.infrastructure.export_formatters import (
    JsonFindingExportFormatter,
    MarkdownFindingExportFormatter,
)
from codelens.plugin.domain.models import ExportResult
from codelens.plugin.domain.ports import ReportSinkPort
from codelens.review.application.export_findings import (
    FindingExportEnvelope,
    FindingExportFormatterPort,
)


class LocalFileExportSink(ReportSinkPort):
    """Write JSON and Markdown exports to ``{repo}/CodeLensReview/``.

    This is the built-in, always-available sink. It cannot be uninstalled.
    The output directory name and formats are configurable via the plugin
    config; defaults are ``CodeLensReview`` and ``["json", "markdown"]``.
    Each attempt uses a unique UTC timestamp in its filenames and ensures the
    configured repository-relative directory is covered by the root .gitignore.
    """

    _DEFAULT_OUTPUT_DIR = "CodeLensReview"
    _DEFAULT_FORMATS = ("json", "markdown")
    _STATE_LOCK: ClassVar[threading.Lock] = threading.Lock()
    _last_exported_at: ClassVar[datetime | None] = None

    def __init__(self) -> None:
        self._formatters: dict[str, FindingExportFormatterPort] = {
            "json": JsonFindingExportFormatter(),
            "markdown": MarkdownFindingExportFormatter(),
        }

    @property
    def sink_id(self) -> str:
        return "local"

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

        def _resolve_paths() -> tuple[Path, Path]:
            return (
                (repository_path / output_dir_name).resolve(),
                repository_path.resolve(),
            )

        target_dir, resolved_repo = await asyncio.to_thread(_resolve_paths)
        if not str(target_dir).startswith(str(resolved_repo) + os.sep):
            return ExportResult(
                plugin_id=self.sink_id,
                task_id=envelope.review.task_id,
                success=False,
                output_path=None,
                error="output_dir must stay within the repository",
                exported_at=datetime.now(UTC),
            )

        try:
            relative_output_dir = target_dir.relative_to(resolved_repo).as_posix()
            await asyncio.to_thread(
                self._ensure_gitignore_entry,
                resolved_repo,
                relative_output_dir,
            )
            await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
        except (OSError, ValueError) as error:
            return ExportResult(
                plugin_id=self.sink_id,
                task_id=envelope.review.task_id,
                success=False,
                output_path=None,
                error=f"Failed to prepare output directory: {error}",
                exported_at=datetime.now(UTC),
            )

        exported_at = await asyncio.to_thread(
            self._select_export_timestamp,
            target_dir,
            tuple(
                formatter.file_extension
                for fmt_id in formats
                if (formatter := self._formatters.get(fmt_id)) is not None
            ),
        )
        filename_timestamp = exported_at.strftime("%Y%m%dT%H%M%S%fZ")
        for fmt_id in formats:
            formatter = self._formatters.get(fmt_id)
            if formatter is None:
                continue
            content = formatter.format(envelope)
            filename = f"findings-{filename_timestamp}.{formatter.file_extension}"
            file_path = target_dir / filename
            try:
                await asyncio.to_thread(self._atomic_write, file_path, content)
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
            exported_at=exported_at,
        )

    @classmethod
    def _select_export_timestamp(
        cls,
        target_dir: Path,
        file_extensions: tuple[str, ...],
    ) -> datetime:
        """Choose a process-unique timestamp whose target filenames do not exist."""

        with cls._STATE_LOCK:
            candidate = datetime.now(UTC)
            if cls._last_exported_at is not None and candidate <= cls._last_exported_at:
                candidate = cls._last_exported_at + timedelta(microseconds=1)
            while any(
                (target_dir / f"findings-{candidate.strftime('%Y%m%dT%H%M%S%fZ')}.{extension}")
                .exists()
                for extension in file_extensions
            ):
                candidate += timedelta(microseconds=1)
            cls._last_exported_at = candidate
            return candidate

    @classmethod
    def _ensure_gitignore_entry(cls, repository: Path, relative_output_dir: str) -> None:
        """Add one root-anchored ignore rule without duplicating effective rules.

        The process-wide lock serializes read-modify-write operations across sink
        instances. A symlinked .gitignore is rejected so export cannot write beyond
        the reviewed repository through a filesystem indirection.
        """

        gitignore = repository / ".gitignore"
        with cls._STATE_LOCK:
            if gitignore.is_symlink():
                raise OSError("repository .gitignore must not be a symbolic link")
            content = gitignore.read_bytes() if gitignore.exists() else b""
            decoded = content.decode("utf-8", errors="surrogateescape")
            ignore_spec = PathSpec.from_lines("gitignore", decoded.splitlines())
            if ignore_spec.match_file(f"{relative_output_dir}/"):
                return

            escaped_path = cls._escape_gitignore_path(relative_output_dir)
            separator = b"" if not content or content.endswith((b"\n", b"\r")) else b"\n"
            updated = content + separator + f"/{escaped_path}/\n".encode()
            existing_mode = (
                stat.S_IMODE(gitignore.stat().st_mode) if gitignore.exists() else None
            )
            cls._atomic_write(gitignore, updated, mode=existing_mode)

    @staticmethod
    def _escape_gitignore_path(relative_path: str) -> str:
        """Escape Git ignore metacharacters while retaining path separators."""

        escaped: list[str] = []
        for character in relative_path:
            if character in {"\\", "!", "#", "[", "]", "*", "?", " "}:
                escaped.append("\\")
            escaped.append(character)
        return "".join(escaped)

    @staticmethod
    def _atomic_write(path: Path, content: bytes, *, mode: int | None = None) -> None:
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
            if mode is not None:
                temporary.chmod(mode)
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

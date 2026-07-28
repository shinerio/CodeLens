"""Ports for the reporting bounded context."""

from pathlib import Path
from typing import Protocol

from codelens.findings.domain.models import Finding
from codelens.reporting.domain.models import (
    ExportResult,
    PluginManifest,
    PluginRecord,
)
from codelens.review.application.export_findings import FindingExportEnvelope
from codelens.review.domain.ports import ReviewExecutionRecord, ReviewRecord


class ReviewExportStorePort(Protocol):
    """Read-only access to review data needed for finding export.

    The reporting context consumes review data (record, execution, findings)
    without depending on the full review store Port. ``SqlReviewStore``
    satisfies this protocol; external mocks can implement it directly.
    """

    async def get_review(self, task_id: str) -> ReviewRecord | None:
        """Return one review summary when it exists."""
        ...

    async def get_execution(self, task_id: str) -> ReviewExecutionRecord | None:
        """Return the execution record (repository path, base/head OIDs)."""
        ...

    async def list_findings(self, task_id: str) -> tuple[Finding, ...]:
        """Return validated findings for one review."""
        ...


class ReportSinkPort(Protocol):
    """Deliver a finding export envelope to one output target.

    A sink is the extension point for report plugins. Built-in sinks live in
    ``reporting.infrastructure``; externally installed sinks are loaded by
    ``PluginLoaderPort`` and must implement this protocol.
    """

    @property
    def sink_id(self) -> str:
        """Return the stable identifier matching the plugin manifest."""
        ...

    @property
    def display_name(self) -> str:
        """Return a human-readable name for UI display."""
        ...

    async def export(
        self,
        envelope: FindingExportEnvelope,
        config: dict,
        repository_path: Path,
    ) -> ExportResult:
        """Deliver the envelope and return a structured result.

        Implementations must not raise; failures should be captured in the
        returned ``ExportResult`` so that one sink's failure does not block
        other sinks during auto-export.
        """
        ...


class PluginStorePort(Protocol):
    """Persist plugin installation and configuration state."""

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        """Return all installed plugins (built-in and external)."""
        ...

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        """Return one plugin record or ``None`` if not installed."""
        ...

    async def save_plugin(self, record: PluginRecord) -> None:
        """Create or update a plugin record atomically."""
        ...

    async def delete_plugin(self, plugin_id: str) -> bool:
        """Remove an external plugin record; return ``False`` if not found."""
        ...


class PluginLoaderPort(Protocol):
    """Load a sink instance from a plugin manifest and install path."""

    def load_sink(
        self,
        manifest: PluginManifest,
        install_path: Path,
    ) -> ReportSinkPort:
        """Instantiate and return the sink declared by the manifest."""
        ...


class RevisionReaderPort(Protocol):
    """Read source code at a pinned Git revision (reused from review context)."""

    async def read_revision_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
    ) -> bytes | None:
        """Return file bytes at the revision, or ``None`` if absent."""
        ...

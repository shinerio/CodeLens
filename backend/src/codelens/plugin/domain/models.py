"""Unified plugin domain models.

A CodeLens plugin is a single install unit that declares a ``platform`` and
optionally provides Trigger and/or Report capabilities. The models in this
module are the single source of truth for plugin identity, capability
declaration, and installation state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from codelens.plugin.domain.versioning import PluginApiVersion


class HookEvent(StrEnum):
    """Events that can trigger a review.

    ``POST_COMMIT`` and ``PRE_PUSH`` are emitted by local git hook scripts.
    ``WEBHOOK`` is emitted by remote platforms via HTTP webhook endpoints.
    """

    POST_COMMIT = "post-commit"
    PRE_PUSH = "pre-push"
    WEBHOOK = "webhook"


@dataclass(frozen=True)
class TriggerCapability:
    """Declare a plugin's trigger capability.

    Attributes:
        trigger_type: Mechanism used to receive events (``"local-hook"`` or
            ``"webhook"``).
        supported_events: Events the trigger can handle.
        entry_point: ``"module:ClassName"`` pointer resolved by the loader.
        config_schema: JSON Schema describing the trigger configuration.
    """

    trigger_type: str
    supported_events: tuple[str, ...]
    entry_point: str
    config_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportCapability:
    """Declare a plugin's report capability.

    Attributes:
        entry_point: ``"module:ClassName"`` pointer resolved by the loader.
        config_schema: JSON Schema describing the report configuration.
    """

    entry_point: str
    config_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManualReviewCapability:
    """Declare a plugin's manual-review capability.

    Enables user-initiated review creation from an external source URL
    (e.g. a CodeHub MR URL) without requiring the webhook trigger to be
    enabled. The loaded instance implements ``ManualReviewSourcePort``.

    Attributes:
        entry_point: ``"module:ClassName"`` pointer resolved by the loader.
        config_schema: JSON Schema describing the manual-review configuration.
    """

    entry_point: str
    config_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginManifest:
    """Declare a plugin's identity, platform, and capabilities.

    The manifest is the stable contract between CodeLens and a plugin. It is
    read from ``plugin.json`` at install time and persisted alongside the
    installation record.

    ``capabilities`` maps capability names (``"trigger"``, ``"report"``,
    ``"manual_review"``) to their declaration dataclasses. A plugin may
    declare any combination.

    ``name_i18n`` and ``description_i18n`` are optional locale-keyed dicts
    (e.g. ``{"zh-CN": "..."}``) that override ``name``/``description`` when
    the frontend locale matches.
    """

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    platform: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    min_codelens_version: str | None = None
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)
    plugin_api_version: PluginApiVersion = PluginApiVersion.V2

    @property
    def trigger(self) -> TriggerCapability | None:
        value = self.capabilities.get("trigger")
        return value if isinstance(value, TriggerCapability) else None

    @property
    def report(self) -> ReportCapability | None:
        value = self.capabilities.get("report")
        return value if isinstance(value, ReportCapability) else None

    @property
    def manual_review(self) -> ManualReviewCapability | None:
        value = self.capabilities.get("manual_review")
        return value if isinstance(value, ManualReviewCapability) else None


@dataclass(frozen=True, slots=True)
class PluginProfileSource:
    """Describe the Core-owned Profile copied into an installed plugin config.

    Provenance is deliberately stored beside plugin-owned configuration. It is
    never passed to plugin code and has no effect on review fingerprints.
    """

    profile_id: str
    profile_name: str
    profile_revision: int
    copied_at: datetime


@dataclass(frozen=True)
class PluginRecord:
    """Persisted installation state for one plugin.

    Built-in plugins have ``install_path=None`` and ``is_builtin=True``.
    External plugins store their on-disk install path and the Git source
    URL/reference used for installation, enabling in-place updates.

    Trigger, report, and manual_review capabilities have independent enable
    flags and independent configuration dicts so that a single plugin can
    evolve each capability without disturbing the other.

    The report capability requires at least one of trigger or manual_review
    to be enabled — either can provide the ``external_context`` needed to
    route findings back to the originating platform.
    """

    plugin_id: str
    manifest: PluginManifest
    is_builtin: bool
    install_path: str | None
    trigger_enabled: bool
    report_enabled: bool
    report_auto_export: bool
    trigger_config: dict[str, Any] = field(default_factory=dict)
    report_config: dict[str, Any] = field(default_factory=dict)
    manual_review_enabled: bool = False
    manual_review_config: dict[str, Any] = field(default_factory=dict)
    git_url: str | None = None
    git_ref: str | None = None
    config_revision: int = 1
    profile_source: PluginProfileSource | None = None


class PluginCapabilityError(ValueError):
    """Raised when a capability toggle violates plugin rules."""


class PluginConfigurationError(ValueError):
    """Raised when capability configuration violates its manifest schema."""


class PluginInstallError(ValueError):
    """Raised when an external plugin cannot be installed or removed."""


def validate_capability_toggle(
    record: PluginRecord,
    *,
    enable_trigger: bool | None = None,
    enable_report: bool | None = None,
    enable_manual_review: bool | None = None,
) -> None:
    """Validate that a capability toggle is legal for the given plugin.

    Built-in plugins have no dependency constraints between capabilities.
    External plugins require at least one of trigger or manual_review to be
    enabled whenever report is enabled — either capability can provide the
    ``external_context`` needed to route findings back to the originating
    platform.

    Raises:
        PluginCapabilityError: If the resulting state would be invalid.
    """

    if record.is_builtin:
        return

    final_trigger = enable_trigger if enable_trigger is not None else record.trigger_enabled
    final_report = enable_report if enable_report is not None else record.report_enabled
    final_manual_review = (
        enable_manual_review if enable_manual_review is not None else record.manual_review_enabled
    )

    if final_report and not final_trigger and not final_manual_review:
        raise PluginCapabilityError(
            f"External plugin '{record.plugin_id}': "
            "report capability requires trigger or manual_review to be enabled"
        )


@dataclass(frozen=True)
class ExportResult:
    """Outcome of one plugin export attempt for one review task.

    A failed export carries ``error`` and ``output_path=None``; callers should
    never assume the path exists without checking ``success``.
    """

    plugin_id: str
    task_id: str
    success: bool
    output_path: str | None
    error: str | None
    exported_at: datetime


@dataclass(frozen=True)
class ExportHistoryEntry:
    """One persisted export attempt, used for listing prior results."""

    plugin_id: str
    task_id: str
    success: bool
    output_path: str | None
    error: str | None
    exported_at: datetime

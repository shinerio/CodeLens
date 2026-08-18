"""Tests for the manual_review capability domain model and constraint validation."""

from dataclasses import replace

import pytest

from codelens.plugin.domain.models import (
    ManualReviewCapability,
    PluginCapabilityError,
    PluginManifest,
    PluginRecord,
    ReportCapability,
    TriggerCapability,
    validate_capability_toggle,
)
from codelens.plugin.domain.versioning import PluginApiVersion


def _external_manifest() -> PluginManifest:
    """Build a manifest with trigger, report, and manual_review capabilities."""
    return PluginManifest(
        plugin_id="external-multi",
        name="Multi-capability plugin",
        version="2.1.0",
        description="",
        author="test",
        platform="local",
        capabilities={
            "trigger": TriggerCapability(
                trigger_type="webhook",
                supported_events=["webhook"],
                entry_point="trigger:Trigger",
                config_schema={},
            ),
            "report": ReportCapability(
                entry_point="report:Sink",
                config_schema={},
            ),
            "manual_review": ManualReviewCapability(
                entry_point="trigger:Trigger",
                config_schema={},
            ),
        },
        min_codelens_version="0.2.0",
        plugin_api_version=PluginApiVersion.V2,
    )


def _external_record(
    *,
    trigger_enabled: bool = False,
    report_enabled: bool = False,
    manual_review_enabled: bool = False,
) -> PluginRecord:
    """Build an external (non-builtin) plugin record."""
    manifest = _external_manifest()
    return PluginRecord(
        plugin_id=manifest.plugin_id,
        manifest=manifest,
        is_builtin=False,
        install_path="/tmp/plugin",
        trigger_enabled=trigger_enabled,
        report_enabled=report_enabled,
        report_auto_export=False,
        trigger_config={},
        report_config={},
        manual_review_enabled=manual_review_enabled,
        manual_review_config={},
    )


def _builtin_record() -> PluginRecord:
    """Build a builtin plugin record (no constraint checks)."""
    record = _external_record()
    return replace(record, is_builtin=True)


def test_manual_review_capability_round_trip() -> None:
    """ManualReviewCapability stores entry_point and config_schema."""
    cap = ManualReviewCapability(
        entry_point="codehub_trigger:CodehubTrigger",
        config_schema={"type": "object", "properties": {}},
    )
    assert cap.entry_point == "codehub_trigger:CodehubTrigger"
    assert cap.config_schema["type"] == "object"


def test_manifest_exposes_manual_review_property() -> None:
    """PluginManifest.manual_review returns the capability or None."""
    manifest = _external_manifest()
    assert manifest.manual_review is not None
    assert manifest.manual_review.entry_point == "trigger:Trigger"

    manifest_without = replace(
        manifest,
        capabilities={
            "trigger": manifest.capabilities["trigger"],
            "report": manifest.capabilities["report"],
        },
    )
    assert manifest_without.manual_review is None


def test_plugin_record_defaults_manual_review_fields() -> None:
    """PluginRecord defaults manual_review_enabled=False, config={}."""
    manifest = PluginManifest(
        plugin_id="test",
        name="test",
        version="1.0",
        description="",
        author="t",
        platform="local",
        capabilities={},
    )
    record = PluginRecord(
        plugin_id=manifest.plugin_id,
        manifest=manifest,
        is_builtin=False,
        install_path=None,
        trigger_enabled=False,
        report_enabled=False,
        report_auto_export=False,
        trigger_config={},
        report_config={},
    )
    assert record.manual_review_enabled is False
    assert record.manual_review_config == {}


def test_validate_all_disabled_is_allowed() -> None:
    """No capabilities enabled is always valid."""
    record = _external_record()
    validate_capability_toggle(record)  # should not raise


def test_validate_report_requires_trigger_or_manual_review() -> None:
    """Report without trigger or manual_review raises on external plugins."""
    record = _external_record(report_enabled=True)
    with pytest.raises(PluginCapabilityError, match="requires trigger or manual_review"):
        validate_capability_toggle(record)


def test_validate_report_with_trigger_only_is_allowed() -> None:
    """Report with trigger enabled is valid (original constraint)."""
    record = _external_record(trigger_enabled=True, report_enabled=True)
    validate_capability_toggle(record)  # should not raise


def test_validate_report_with_manual_review_only_is_allowed() -> None:
    """Report with manual_review enabled (no trigger) is valid — relaxed constraint."""
    record = _external_record(manual_review_enabled=True, report_enabled=True)
    validate_capability_toggle(record)  # should not raise


def test_validate_report_with_both_trigger_and_manual_review() -> None:
    """Report with both trigger and manual_review is valid."""
    record = _external_record(
        trigger_enabled=True,
        manual_review_enabled=True,
        report_enabled=True,
    )
    validate_capability_toggle(record)


def test_validate_builtin_plugin_skips_constraint() -> None:
    """Builtin plugins have no dependency constraints."""
    record = replace(_builtin_record(), report_enabled=True)
    validate_capability_toggle(record)  # should not raise


def test_validate_enable_report_without_trigger_or_manual_raises() -> None:
    """Enabling report when both trigger and manual_review are off raises."""
    record = _external_record()
    with pytest.raises(PluginCapabilityError):
        validate_capability_toggle(record, enable_report=True)


def test_validate_enable_report_with_manual_review_enabled() -> None:
    """Enabling report when manual_review is already enabled is valid."""
    record = _external_record(manual_review_enabled=True)
    validate_capability_toggle(record, enable_report=True)  # should not raise


def test_validate_disabling_manual_review_with_report_raises() -> None:
    """Disabling manual_review when report is on and trigger is off raises."""
    record = _external_record(
        manual_review_enabled=True,
        report_enabled=True,
    )
    with pytest.raises(PluginCapabilityError):
        validate_capability_toggle(record, enable_manual_review=False)


def test_validate_disabling_manual_review_with_trigger_stays_valid() -> None:
    """Disabling manual_review is fine when trigger is still enabled."""
    record = _external_record(
        trigger_enabled=True,
        manual_review_enabled=True,
        report_enabled=True,
    )
    validate_capability_toggle(record, enable_manual_review=False)

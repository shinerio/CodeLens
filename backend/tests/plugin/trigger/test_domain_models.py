"""Tests for trigger plugin domain models."""

from pathlib import Path

import pytest

from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
    TriggerManifest,
    TriggerRecord,
    TriggerType,
)


def test_hook_event_values() -> None:
    """HookEvent enum should have correct string values."""
    assert HookEvent.POST_COMMIT.value == "post-commit"
    assert HookEvent.PRE_PUSH.value == "pre-push"


def test_trigger_type_values() -> None:
    """TriggerType enum should have correct string values."""
    assert TriggerType.LOCAL_HOOK.value == "local-hook"
    assert TriggerType.WEBHOOK.value == "webhook"


def test_trigger_manifest_creation() -> None:
    """TriggerManifest should be created with all required fields."""
    manifest = TriggerManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        entry_point="module:Class",
        trigger_type=TriggerType.LOCAL_HOOK,
        supported_events=(HookEvent.POST_COMMIT,),
    )

    assert manifest.plugin_id == "test-plugin"
    assert manifest.name == "Test Plugin"
    assert manifest.version == "1.0.0"
    assert manifest.trigger_type == TriggerType.LOCAL_HOOK
    assert manifest.supported_events == (HookEvent.POST_COMMIT,)
    assert manifest.config_schema == {}
    assert manifest.min_codelens_version is None


def test_trigger_config_creation() -> None:
    """TriggerConfig should be created with all required fields."""
    config = TriggerConfig(
        repository_paths=("/repo1", "/repo2"),
        events=(HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=10,
    )

    assert config.repository_paths == ("/repo1", "/repo2")
    assert config.events == (HookEvent.POST_COMMIT, HookEvent.PRE_PUSH)
    assert config.scope_type == "commit"
    assert config.debounce_seconds == 10
    assert config.extra == {}


def test_trigger_record_creation() -> None:
    """TriggerRecord should be created with all required fields."""
    manifest = TriggerManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        entry_point="module:Class",
        trigger_type=TriggerType.LOCAL_HOOK,
        supported_events=(HookEvent.POST_COMMIT,),
    )

    config = TriggerConfig(
        repository_paths=("/repo1",),
        events=(HookEvent.POST_COMMIT,),
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=10,
    )

    record = TriggerRecord(
        plugin_id="test-plugin",
        manifest=manifest,
        is_enabled=True,
        is_builtin=False,
        install_path="/path/to/plugin",
        config=config,
    )

    assert record.plugin_id == "test-plugin"
    assert record.is_enabled is True
    assert record.is_builtin is False
    assert record.install_path == "/path/to/plugin"
    assert record.manifest.plugin_id == "test-plugin"
    assert record.config.scope_type == "commit"


def test_trigger_manifest_with_config_schema() -> None:
    """TriggerManifest should support config_schema and min_codelens_version."""
    config_schema = {
        "type": "object",
        "properties": {
            "debounce_seconds": {"type": "integer", "minimum": 0},
        },
    }

    manifest = TriggerManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        entry_point="module:Class",
        trigger_type=TriggerType.LOCAL_HOOK,
        supported_events=(HookEvent.POST_COMMIT,),
        config_schema=config_schema,
        min_codelens_version="1.0.0",
    )

    assert manifest.config_schema == config_schema
    assert manifest.min_codelens_version == "1.0.0"


def test_trigger_config_with_extra() -> None:
    """TriggerConfig should support extra configuration fields."""
    config = TriggerConfig(
        repository_paths=("/repo1",),
        events=(HookEvent.POST_COMMIT,),
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=10,
        extra={"custom_field": "custom_value"},
    )

    assert config.extra == {"custom_field": "custom_value"}

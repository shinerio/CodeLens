"""Tests for FilesystemTriggerStore."""

import tempfile
from pathlib import Path

import pytest

from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
    TriggerManifest,
    TriggerRecord,
    TriggerType,
)
from codelens.trigger.infrastructure.trigger_store import (
    FilesystemTriggerStore,
)


@pytest.fixture
def temp_data_dir() -> Path:
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def store(temp_data_dir: Path) -> FilesystemTriggerStore:
    """Create a FilesystemTriggerStore with temporary directory."""
    return FilesystemTriggerStore(temp_data_dir)


@pytest.fixture
def sample_manifest() -> TriggerManifest:
    """Create a sample trigger manifest."""
    return TriggerManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        entry_point="module:Class",
        trigger_type=TriggerType.LOCAL_HOOK,
        supported_events=(HookEvent.POST_COMMIT,),
    )


@pytest.fixture
def sample_config() -> TriggerConfig:
    """Create a sample trigger config."""
    return TriggerConfig(
        repository_paths=("/repo1",),
        events=(HookEvent.POST_COMMIT,),
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=10,
    )


@pytest.fixture
def sample_record(sample_manifest: TriggerManifest, sample_config: TriggerConfig) -> TriggerRecord:
    """Create a sample trigger record."""
    return TriggerRecord(
        plugin_id="test-plugin",
        manifest=sample_manifest,
        is_enabled=True,
        is_builtin=False,
        install_path="/path/to/plugin",
        config=sample_config,
    )


@pytest.mark.asyncio
async def test_list_triggers_empty(store: FilesystemTriggerStore) -> None:
    """list_triggers should return empty tuple when no triggers exist."""
    triggers = await store.list_triggers()
    assert triggers == ()


@pytest.mark.asyncio
async def test_save_and_get_trigger(
    store: FilesystemTriggerStore,
    sample_record: TriggerRecord,
) -> None:
    """save_trigger should persist and get_trigger should retrieve."""
    await store.save_trigger(sample_record)

    retrieved = await store.get_trigger("test-plugin")
    assert retrieved is not None
    assert retrieved.plugin_id == "test-plugin"
    assert retrieved.manifest.name == "Test Plugin"
    assert retrieved.config.scope_type == "commit"
    assert retrieved.is_enabled is True


@pytest.mark.asyncio
async def test_list_triggers_after_save(
    store: FilesystemTriggerStore,
    sample_record: TriggerRecord,
) -> None:
    """list_triggers should return saved triggers."""
    await store.save_trigger(sample_record)

    triggers = await store.list_triggers()
    assert len(triggers) == 1
    assert triggers[0].plugin_id == "test-plugin"


@pytest.mark.asyncio
async def test_update_trigger(
    store: FilesystemTriggerStore,
    sample_record: TriggerRecord,
) -> None:
    """save_trigger should update existing trigger."""
    await store.save_trigger(sample_record)

    updated = TriggerRecord(
        plugin_id="test-plugin",
        manifest=sample_record.manifest,
        is_enabled=False,
        is_builtin=False,
        install_path="/path/to/plugin",
        config=sample_record.config,
    )
    await store.save_trigger(updated)

    retrieved = await store.get_trigger("test-plugin")
    assert retrieved is not None
    assert retrieved.is_enabled is False


@pytest.mark.asyncio
async def test_delete_trigger(
    store: FilesystemTriggerStore,
    sample_record: TriggerRecord,
) -> None:
    """delete_trigger should remove trigger."""
    await store.save_trigger(sample_record)

    deleted = await store.delete_trigger("test-plugin")
    assert deleted is True

    retrieved = await store.get_trigger("test-plugin")
    assert retrieved is None


@pytest.mark.asyncio
async def test_delete_nonexistent_trigger(store: FilesystemTriggerStore) -> None:
    """delete_trigger should return False for nonexistent trigger."""
    deleted = await store.delete_trigger("nonexistent")
    assert deleted is False


@pytest.mark.asyncio
async def test_get_nonexistent_trigger(store: FilesystemTriggerStore) -> None:
    """get_trigger should return None for nonexistent trigger."""
    retrieved = await store.get_trigger("nonexistent")
    assert retrieved is None


@pytest.mark.asyncio
async def test_multiple_triggers(
    store: FilesystemTriggerStore,
    sample_manifest: TriggerManifest,
    sample_config: TriggerConfig,
) -> None:
    """Store should handle multiple triggers."""
    record1 = TriggerRecord(
        plugin_id="plugin1",
        manifest=sample_manifest,
        is_enabled=True,
        is_builtin=False,
        install_path="/path1",
        config=sample_config,
    )
    record2 = TriggerRecord(
        plugin_id="plugin2",
        manifest=sample_manifest,
        is_enabled=False,
        is_builtin=True,
        install_path=None,
        config=sample_config,
    )

    await store.save_trigger(record1)
    await store.save_trigger(record2)

    triggers = await store.list_triggers()
    assert len(triggers) == 2
    plugin_ids = {t.plugin_id for t in triggers}
    assert plugin_ids == {"plugin1", "plugin2"}

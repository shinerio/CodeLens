"""Tests for TriggerManager."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from codelens.trigger.application.trigger_manager import TriggerPluginManager
from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
    TriggerManifest,
    TriggerRecord,
    TriggerType,
)
from codelens.trigger.domain.ports import TriggerStorePort


@pytest.fixture
def mock_store() -> Mock:
    """Create a mock TriggerStorePort."""
    mock = Mock(spec=TriggerStorePort)
    mock.list_triggers = AsyncMock(return_value=[])
    mock.get_trigger = AsyncMock(return_value=None)
    mock.save_trigger = AsyncMock()
    mock.delete_trigger = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def manager(mock_store: Mock) -> TriggerPluginManager:
    """Create a TriggerPluginManager with mock store."""
    return TriggerPluginManager(mock_store)


@pytest.fixture
def sample_record() -> TriggerRecord:
    """Create a sample trigger record."""
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
    return TriggerRecord(
        plugin_id="test-plugin",
        manifest=manifest,
        is_enabled=True,
        is_builtin=False,
        install_path="/path/to/plugin",
        config=config,
    )


@pytest.mark.asyncio
async def test_list_plugins(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """list_plugins should return all plugins from store."""
    mock_store.list_triggers.return_value = [sample_record]

    plugins = await manager.list_plugins()

    assert len(plugins) == 1
    assert plugins[0].plugin_id == "test-plugin"
    mock_store.list_triggers.assert_called_once()


@pytest.mark.asyncio
async def test_get_plugin(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """get_plugin should return specific plugin from store."""
    mock_store.get_trigger.return_value = sample_record

    plugin = await manager.get_plugin("test-plugin")

    assert plugin is not None
    assert plugin.plugin_id == "test-plugin"
    mock_store.get_trigger.assert_called_once_with("test-plugin")


@pytest.mark.asyncio
async def test_enable_plugin(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """enable_plugin should update is_enabled to True."""
    disabled_record = TriggerRecord(
        plugin_id=sample_record.plugin_id,
        manifest=sample_record.manifest,
        is_enabled=False,
        is_builtin=sample_record.is_builtin,
        install_path=sample_record.install_path,
        config=sample_record.config,
    )
    mock_store.get_trigger.return_value = disabled_record

    result = await manager.enable_plugin("test-plugin")

    assert result is not None
    assert result.is_enabled is True
    mock_store.save_trigger.assert_called_once()
    saved_record = mock_store.save_trigger.call_args[0][0]
    assert saved_record.is_enabled is True


@pytest.mark.asyncio
async def test_enable_plugin_not_found(
    manager: TriggerPluginManager,
    mock_store: Mock,
) -> None:
    """enable_plugin should return None if plugin not found."""
    mock_store.get_trigger.return_value = None

    result = await manager.enable_plugin("nonexistent")

    assert result is None
    mock_store.save_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_disable_plugin(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """disable_plugin should update is_enabled to False."""
    mock_store.get_trigger.return_value = sample_record

    result = await manager.disable_plugin("test-plugin")

    assert result is not None
    assert result.is_enabled is False
    mock_store.save_trigger.assert_called_once()
    saved_record = mock_store.save_trigger.call_args[0][0]
    assert saved_record.is_enabled is False


@pytest.mark.asyncio
async def test_update_config(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """update_config should update plugin configuration."""
    mock_store.get_trigger.return_value = sample_record

    new_config = TriggerConfig(
        repository_paths=("/repo2",),
        events=(HookEvent.PRE_PUSH,),
        scope_type="branch",
        base_ref="main",
        target_ref="feature",
        selected_agents=("agent2",),
        prompt_locale="zh-CN",
        debounce_seconds=20,
    )

    result = await manager.update_config("test-plugin", new_config)

    assert result is not None
    assert result.config.repository_paths == ("/repo2",)
    assert result.config.events == (HookEvent.PRE_PUSH,)
    assert result.config.scope_type == "branch"
    mock_store.save_trigger.assert_called_once()


@pytest.mark.asyncio
async def test_update_config_not_found(
    manager: TriggerPluginManager,
    mock_store: Mock,
) -> None:
    """update_config should return None if plugin not found."""
    mock_store.get_trigger.return_value = None

    new_config = TriggerConfig(
        repository_paths=("/repo2",),
        events=(HookEvent.PRE_PUSH,),
        scope_type="branch",
        base_ref="main",
        target_ref="feature",
        selected_agents=("agent2",),
        prompt_locale="zh-CN",
        debounce_seconds=20,
    )

    result = await manager.update_config("nonexistent", new_config)

    assert result is None
    mock_store.save_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_uninstall_plugin(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """uninstall_plugin should delete plugin from store."""
    mock_store.get_trigger.return_value = sample_record

    result = await manager.uninstall_plugin("test-plugin")

    assert result is True
    mock_store.delete_trigger.assert_called_once_with("test-plugin")


@pytest.mark.asyncio
async def test_uninstall_plugin_not_found(
    manager: TriggerPluginManager,
    mock_store: Mock,
) -> None:
    """uninstall_plugin should return False if plugin not found."""
    mock_store.get_trigger.return_value = None

    result = await manager.uninstall_plugin("nonexistent")

    assert result is False
    mock_store.delete_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_uninstall_builtin_plugin_fails(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """uninstall_plugin should fail for builtin plugins."""
    builtin_record = TriggerRecord(
        plugin_id=sample_record.plugin_id,
        manifest=sample_record.manifest,
        is_enabled=sample_record.is_enabled,
        is_builtin=True,  # Builtin
        install_path=sample_record.install_path,
        config=sample_record.config,
    )
    mock_store.get_trigger.return_value = builtin_record

    result = await manager.uninstall_plugin("test-plugin")

    assert result is False
    mock_store.delete_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_builtin_plugins(
    manager: TriggerPluginManager,
    mock_store: Mock,
) -> None:
    """initialize_builtin_plugins should create builtin plugin if not exists."""
    mock_store.get_trigger.return_value = None

    await manager.initialize_builtin_plugins()

    mock_store.save_trigger.assert_called_once()
    saved_record = mock_store.save_trigger.call_args[0][0]
    assert saved_record.plugin_id == "local-git-hook"
    assert saved_record.is_builtin is True
    assert saved_record.is_enabled is False


@pytest.mark.asyncio
async def test_initialize_builtin_plugins_already_exists(
    manager: TriggerPluginManager,
    mock_store: Mock,
    sample_record: TriggerRecord,
) -> None:
    """initialize_builtin_plugins should not recreate if already exists."""
    mock_store.get_trigger.return_value = sample_record

    await manager.initialize_builtin_plugins()

    mock_store.save_trigger.assert_not_called()

"""Tests for TriggerOrchestrator."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from codelens.trigger.application.trigger_orchestrator import (
    TriggerOrchestrator,
)
from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
    TriggerManifest,
    TriggerRecord,
    TriggerType,
)
from codelens.trigger.domain.ports import (
    ReviewCreatorPort,
    TriggerPluginLoaderPort,
    TriggerStorePort,
)
from codelens.plugin.trigger.local_hook.local_hook_trigger import (
    LocalHookTriggerAdapter,
)


@pytest.fixture
def mock_store() -> Mock:
    """Create a mock TriggerStorePort."""
    mock = Mock(spec=TriggerStorePort)
    mock.list_triggers = AsyncMock(return_value=())
    return mock


@pytest.fixture
def mock_review_creator() -> Mock:
    """Create a mock ReviewCreatorPort."""
    mock = Mock(spec=ReviewCreatorPort)
    mock.create_review_from_trigger = AsyncMock(return_value="review_123")
    return mock


@pytest.fixture
def mock_plugin_loader(mock_review_creator: Mock) -> Mock:
    """Create a mock TriggerPluginLoaderPort."""
    mock = Mock(spec=TriggerPluginLoaderPort)

    def load_plugin_side_effect(plugin_id: str, review_creator: Mock) -> LocalHookTriggerAdapter:
        if plugin_id == "local-git-hook":
            return LocalHookTriggerAdapter(review_creator)
        raise ValueError(f"Unsupported plugin: {plugin_id}")

    mock.load_plugin = Mock(side_effect=load_plugin_side_effect)
    return mock


@pytest.fixture
def orchestrator(
    mock_store: Mock,
    mock_review_creator: Mock,
    mock_plugin_loader: Mock,
) -> TriggerOrchestrator:
    """Create a TriggerOrchestrator with mocks."""
    return TriggerOrchestrator(mock_store, mock_review_creator, mock_plugin_loader)


@pytest.fixture
def sample_config() -> TriggerConfig:
    """Create a sample trigger config."""
    return TriggerConfig(
        repository_paths=("/repo1",),
        events=(HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=0,
    )


@pytest.fixture
def sample_record(sample_config: TriggerConfig) -> TriggerRecord:
    """Create a sample trigger record for the built-in local hook."""
    manifest = TriggerManifest(
        plugin_id="local-git-hook",
        name="Local Git Hook Trigger",
        version="1.0.0",
        description="Built-in local git hook trigger",
        author="CodeLens",
        entry_point="local_hook_trigger:LocalHookTriggerAdapter",
        trigger_type=TriggerType.LOCAL_HOOK,
        supported_events=(HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
    )
    return TriggerRecord(
        plugin_id="local-git-hook",
        manifest=manifest,
        is_enabled=True,
        is_builtin=True,
        install_path=None,
        config=sample_config,
    )


@pytest.fixture
def temp_repo() -> Path:
    """Create a temporary repository path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.mark.asyncio
async def test_handle_event_no_enabled_plugins(
    orchestrator: TriggerOrchestrator,
    mock_store: Mock,
    temp_repo: Path,
) -> None:
    """handle_event should return empty tuple when no plugins are enabled."""
    mock_store.list_triggers.return_value = ()

    results = await orchestrator.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        {"commit_sha": "abc123"},
    )

    assert results == ()


@pytest.mark.asyncio
async def test_handle_event_plugin_not_configured_for_repo(
    orchestrator: TriggerOrchestrator,
    mock_store: Mock,
    sample_record: TriggerRecord,
    temp_repo: Path,
) -> None:
    """handle_event should skip plugins not configured for the repository."""
    # Plugin is configured for /repo1, but we're using temp_repo
    mock_store.list_triggers.return_value = (sample_record,)

    results = await orchestrator.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        {"commit_sha": "abc123"},
    )

    assert results == ()


@pytest.mark.asyncio
async def test_handle_event_plugin_disabled(
    orchestrator: TriggerOrchestrator,
    mock_store: Mock,
    sample_record: TriggerRecord,
    temp_repo: Path,
) -> None:
    """handle_event should skip disabled plugins."""
    disabled_record = TriggerRecord(
        plugin_id=sample_record.plugin_id,
        manifest=sample_record.manifest,
        is_enabled=False,
        is_builtin=sample_record.is_builtin,
        install_path=sample_record.install_path,
        config=TriggerConfig(
            repository_paths=(str(temp_repo),),
            events=(HookEvent.POST_COMMIT,),
            scope_type="commit",
            base_ref=None,
            target_ref=None,
            selected_agents=("agent1",),
            prompt_locale="en",
            debounce_seconds=0,
        ),
    )
    mock_store.list_triggers.return_value = (disabled_record,)

    results = await orchestrator.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        {"commit_sha": "abc123"},
    )

    assert results == ()


@pytest.mark.asyncio
async def test_handle_event_invokes_plugin(
    orchestrator: TriggerOrchestrator,
    mock_store: Mock,
    mock_review_creator: Mock,
    sample_record: TriggerRecord,
    temp_repo: Path,
) -> None:
    """handle_event should invoke matching plugin and return task_id."""
    updated_config = TriggerConfig(
        repository_paths=(str(temp_repo),),
        events=(HookEvent.POST_COMMIT,),
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=0,
    )
    updated_record = TriggerRecord(
        plugin_id=sample_record.plugin_id,
        manifest=sample_record.manifest,
        is_enabled=True,
        is_builtin=True,
        install_path=None,
        config=updated_config,
    )
    mock_store.list_triggers.return_value = (updated_record,)

    results = await orchestrator.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        {"commit_sha": "abc123"},
    )

    assert len(results) == 1
    assert results[0] == "review_123"
    mock_review_creator.create_review_from_trigger.assert_called_once_with(
        repository_path=temp_repo,
        scope_type="commit",
        scope_params={"base_commit": "abc123~1", "target_ref": "abc123"},
        selected_agents=("agent1",),
        prompt_locale="en",
    )


@pytest.mark.asyncio
async def test_handle_event_multiple_plugins(
    orchestrator: TriggerOrchestrator,
    mock_store: Mock,
    sample_record: TriggerRecord,
    temp_repo: Path,
) -> None:
    """handle_event should invoke all matching plugins."""
    config = TriggerConfig(
        repository_paths=(str(temp_repo),),
        events=(HookEvent.POST_COMMIT,),
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=0,
    )
    record1 = TriggerRecord(
        plugin_id="local-git-hook",
        manifest=sample_record.manifest,
        is_enabled=True,
        is_builtin=True,
        install_path=None,
        config=config,
    )
    # Two instances of the same built-in plugin type
    record2 = TriggerRecord(
        plugin_id="local-git-hook",
        manifest=sample_record.manifest,
        is_enabled=True,
        is_builtin=True,
        install_path=None,
        config=config,
    )
    mock_store.list_triggers.return_value = (record1, record2)

    results = await orchestrator.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        {"commit_sha": "abc123"},
    )

    assert len(results) == 2


@pytest.mark.asyncio
async def test_handle_event_unsupported_plugin_type(
    orchestrator: TriggerOrchestrator,
    mock_store: Mock,
    sample_record: TriggerRecord,
    temp_repo: Path,
) -> None:
    """handle_event should handle unsupported plugin types gracefully."""
    unknown_record = TriggerRecord(
        plugin_id="unknown-plugin",
        manifest=sample_record.manifest,
        is_enabled=True,
        is_builtin=False,
        install_path="/path/to/unknown",
        config=TriggerConfig(
            repository_paths=(str(temp_repo),),
            events=(HookEvent.POST_COMMIT,),
            scope_type="commit",
            base_ref=None,
            target_ref=None,
            selected_agents=("agent1",),
            prompt_locale="en",
            debounce_seconds=0,
        ),
    )
    mock_store.list_triggers.return_value = (unknown_record,)

    # Should not raise, should return tuple with None for failed plugin
    results = await orchestrator.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        {"commit_sha": "abc123"},
    )

    assert results == (None,)

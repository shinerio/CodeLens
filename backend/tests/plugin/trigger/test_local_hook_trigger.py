"""Tests for LocalHookTriggerAdapter."""

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
)
from codelens.trigger.domain.ports import ReviewCreatorPort
from codelens.plugin.trigger.local_hook.local_hook_trigger import (
    LocalHookTriggerAdapter,
)


@pytest.fixture
def mock_review_creator() -> Mock:
    """Create a mock ReviewCreatorPort."""
    mock = Mock(spec=ReviewCreatorPort)
    mock.create_review_from_trigger = AsyncMock(return_value="review_123")
    return mock


@pytest.fixture
def adapter(mock_review_creator: Mock) -> LocalHookTriggerAdapter:
    """Create a LocalHookTriggerAdapter with mock review creator."""
    return LocalHookTriggerAdapter(mock_review_creator)


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
        debounce_seconds=0,  # Disable debouncing for tests
    )


@pytest.fixture
def temp_repo() -> Path:
    """Create a temporary repository path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.mark.asyncio
async def test_handle_post_commit_event(
    adapter: LocalHookTriggerAdapter,
    mock_review_creator: Mock,
    sample_config: TriggerConfig,
    temp_repo: Path,
) -> None:
    """handle_event should create review for post-commit event."""
    event_payload = {"commit_sha": "abc123"}

    task_id = await adapter.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        sample_config,
        event_payload,
    )

    assert task_id == "review_123"
    mock_review_creator.create_review_from_trigger.assert_called_once_with(
        repository_path=temp_repo,
        scope_type="commit",
        scope_params={"base_commit": "abc123~1", "target_ref": "abc123"},
        selected_agents=("agent1",),
        prompt_locale="en",
    )


@pytest.mark.asyncio
async def test_handle_pre_push_event(
    adapter: LocalHookTriggerAdapter,
    mock_review_creator: Mock,
    temp_repo: Path,
) -> None:
    """handle_event should create review for pre-push event with branch scope."""
    # Push events should use branch scope, not commit scope
    branch_config = TriggerConfig(
        repository_paths=("/repo1",),
        events=(HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
        scope_type="branch",
        base_ref="main",
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=0,
    )
    event_payload = {"push_ref": "refs/heads/main"}

    task_id = await adapter.handle_event(
        HookEvent.PRE_PUSH,
        temp_repo,
        branch_config,
        event_payload,
    )

    assert task_id == "review_123"
    mock_review_creator.create_review_from_trigger.assert_called_once_with(
        repository_path=temp_repo,
        scope_type="branch",
        scope_params={"base_ref": "main", "target_ref": "refs/heads/main"},
        selected_agents=("agent1",),
        prompt_locale="en",
    )


@pytest.mark.asyncio
async def test_handle_event_not_in_config(
    adapter: LocalHookTriggerAdapter,
    mock_review_creator: Mock,
    temp_repo: Path,
) -> None:
    """handle_event should skip if event not in config."""
    config = TriggerConfig(
        repository_paths=("/repo1",),
        events=(HookEvent.POST_COMMIT,),  # Only post-commit
        scope_type="commit",
        base_ref=None,
        target_ref=None,
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=0,
    )

    task_id = await adapter.handle_event(
        HookEvent.PRE_PUSH,  # Not in config
        temp_repo,
        config,
        {"push_ref": "refs/heads/main"},
    )

    assert task_id is None
    mock_review_creator.create_review_from_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_handle_event_debounced(
    adapter: LocalHookTriggerAdapter,
    mock_review_creator: Mock,
    temp_repo: Path,
) -> None:
    """handle_event should debounce rapid successive events."""
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

    # First event should succeed
    task_id1 = await adapter.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        config,
        {"commit_sha": "abc123"},
    )
    assert task_id1 == "review_123"

    # Second event immediately after should be debounced
    task_id2 = await adapter.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        config,
        {"commit_sha": "def456"},
    )
    assert task_id2 is None

    # Only one review should have been created
    assert mock_review_creator.create_review_from_trigger.call_count == 1


@pytest.mark.asyncio
async def test_handle_event_review_creation_fails(
    adapter: LocalHookTriggerAdapter,
    mock_review_creator: Mock,
    sample_config: TriggerConfig,
    temp_repo: Path,
) -> None:
    """handle_event should return None if review creation fails."""
    mock_review_creator.create_review_from_trigger.side_effect = Exception("Failed")

    task_id = await adapter.handle_event(
        HookEvent.POST_COMMIT,
        temp_repo,
        sample_config,
        {"commit_sha": "abc123"},
    )

    assert task_id is None


@pytest.mark.asyncio
async def test_handle_event_with_branch_scope(
    adapter: LocalHookTriggerAdapter,
    mock_review_creator: Mock,
    temp_repo: Path,
) -> None:
    """handle_event should use branch scope when configured."""
    config = TriggerConfig(
        repository_paths=("/repo1",),
        events=(HookEvent.PRE_PUSH,),
        scope_type="branch",
        base_ref="main",
        target_ref="feature",
        selected_agents=("agent1",),
        prompt_locale="en",
        debounce_seconds=0,
    )

    task_id = await adapter.handle_event(
        HookEvent.PRE_PUSH,
        temp_repo,
        config,
        {"push_ref": "refs/heads/feature"},
    )

    assert task_id == "review_123"
    mock_review_creator.create_review_from_trigger.assert_called_once_with(
        repository_path=temp_repo,
        scope_type="branch",
        scope_params={"base_ref": "main", "target_ref": "feature"},
        selected_agents=("agent1",),
        prompt_locale="en",
    )


def test_trigger_id(adapter: LocalHookTriggerAdapter) -> None:
    """trigger_id should return correct value."""
    assert adapter.trigger_id == "local-git-hook"


def test_display_name(adapter: LocalHookTriggerAdapter) -> None:
    """display_name should return correct value."""
    assert adapter.display_name == "Local Git Hook Trigger"

"""Tests for HookInstaller."""

import os
import tempfile
from pathlib import Path

import pytest

from codelens.trigger.domain.models import HookEvent
from codelens.plugin.trigger.local_hook.hook_installer import (
    HookInstaller,
)


@pytest.fixture
def temp_repo() -> Path:
    """Create a temporary git repository structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        git_dir = repo_path / ".git"
        git_dir.mkdir()
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir()
        yield repo_path


@pytest.fixture
def hook_installer() -> HookInstaller:
    """Create a HookInstaller with the actual plugin directory."""
    plugin_dir = Path(__file__).parent.parent.parent.parent.parent / "backend" / "src" / "codelens" / "plugin" / "trigger" / "local_hook"
    return HookInstaller(plugin_dir)


@pytest.mark.asyncio
async def test_install_hooks_creates_files(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should create hook script files."""
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
        port=8000,
    )

    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    pre_push = temp_repo / ".git" / "hooks" / "pre-push"

    assert post_commit.exists()
    assert pre_push.exists()
    assert os.access(post_commit, os.X_OK)
    assert os.access(pre_push, os.X_OK)


@pytest.mark.asyncio
async def test_install_hooks_replaces_port(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should replace __PORT__ placeholder."""
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=9000,
    )

    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    content = post_commit.read_text()

    assert "9000" in content
    assert "__PORT__" not in content


@pytest.mark.asyncio
async def test_install_hooks_backs_up_existing(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should back up existing non-CodeLens hooks."""
    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    original_content = "#!/bin/bash\necho 'original hook'"
    post_commit.write_text(original_content)
    post_commit.chmod(0o755)

    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=8000,
    )

    backup = temp_repo / ".git" / "hooks" / "post-commit.codelens-backup"
    assert backup.exists()
    assert backup.read_text() == original_content


@pytest.mark.asyncio
async def test_install_hooks_does_not_backup_codelens_hooks(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should not back up existing CodeLens hooks."""
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=8000,
    )

    # Install again
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=9000,
    )

    backup = temp_repo / ".git" / "hooks" / "post-commit.codelens-backup"
    assert not backup.exists()


@pytest.mark.asyncio
async def test_uninstall_hooks_removes_files(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """uninstall_hooks should remove CodeLens hook files."""
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
        port=8000,
    )

    await hook_installer.uninstall_hooks(temp_repo)

    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    pre_push = temp_repo / ".git" / "hooks" / "pre-push"

    assert not post_commit.exists()
    assert not pre_push.exists()


@pytest.mark.asyncio
async def test_uninstall_hooks_restores_backup(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """uninstall_hooks should restore backed up hooks."""
    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    original_content = "#!/bin/bash\necho 'original hook'"
    post_commit.write_text(original_content)
    post_commit.chmod(0o755)

    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=8000,
    )

    await hook_installer.uninstall_hooks(temp_repo)

    assert post_commit.exists()
    assert post_commit.read_text() == original_content


@pytest.mark.asyncio
async def test_uninstall_hooks_ignores_non_codelens(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """uninstall_hooks should not remove non-CodeLens hooks."""
    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    original_content = "#!/bin/bash\necho 'original hook'"
    post_commit.write_text(original_content)
    post_commit.chmod(0o755)

    # Try to uninstall without installing first
    await hook_installer.uninstall_hooks(temp_repo)

    assert post_commit.exists()
    assert post_commit.read_text() == original_content


@pytest.mark.asyncio
async def test_is_installed_returns_correct_status(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """is_installed should return correct installation status."""
    status = await hook_installer.is_installed(temp_repo)
    assert status[HookEvent.POST_COMMIT] is False
    assert status[HookEvent.PRE_PUSH] is False

    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=8000,
    )

    status = await hook_installer.is_installed(temp_repo)
    assert status[HookEvent.POST_COMMIT] is True
    assert status[HookEvent.PRE_PUSH] is False


@pytest.mark.asyncio
async def test_install_hooks_invalid_repo(
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should raise ValueError for invalid repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        invalid_repo = Path(tmpdir)
        with pytest.raises(ValueError, match="Not a git repository"):
            await hook_installer.install_hooks(
                invalid_repo,
                (HookEvent.POST_COMMIT,),
                port=8000,
            )

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
async def test_install_creates_standalone_script(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should create standalone script."""
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=8000,
    )

    standalone = temp_repo / ".git" / "hooks" / "code-lens-review-hook.sh"
    assert standalone.exists()
    assert os.access(standalone, os.X_OK)


@pytest.mark.asyncio
async def test_install_without_user_hook_creates_symlink(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should create symlink when no user hook exists."""
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT, HookEvent.PRE_PUSH),
        port=8000,
    )

    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    pre_push = temp_repo / ".git" / "hooks" / "pre-push"

    assert post_commit.is_symlink()
    assert pre_push.is_symlink()


@pytest.mark.asyncio
async def test_install_with_user_hook_injects_call(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should inject call when user hook exists."""
    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    original_content = "#!/bin/bash\necho 'original hook'"
    post_commit.write_text(original_content)
    post_commit.chmod(0o755)

    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=8000,
    )

    content = post_commit.read_text()
    assert "# CodeLens Trigger Hook" in content
    assert "code-lens-review-hook.sh" in content
    assert "echo 'original hook'" in content


@pytest.mark.asyncio
async def test_install_replaces_port(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """install_hooks should replace __PORT__ placeholder in standalone script."""
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=9000,
    )

    standalone = temp_repo / ".git" / "hooks" / "code-lens-review-hook.sh"
    content = standalone.read_text()

    assert "9000" in content
    assert "__PORT__" not in content


@pytest.mark.asyncio
async def test_uninstall_removes_injected_line_and_script(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """uninstall_hooks should remove injected line and standalone script."""
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

    standalone = temp_repo / ".git" / "hooks" / "code-lens-review-hook.sh"
    assert not standalone.exists()

    content = post_commit.read_text()
    assert "# CodeLens Trigger Hook" not in content
    assert "code-lens-review-hook.sh" not in content


@pytest.mark.asyncio
async def test_uninstall_restores_user_hook_unchanged(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """uninstall_hooks should leave user hook unchanged."""
    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    original_content = "#!/bin/bash\necho 'original hook'\nexit 0"
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
async def test_uninstall_removes_symlink(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """uninstall_hooks should remove symlink."""
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
async def test_uninstall_ignores_non_codelens(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """uninstall_hooks should not modify non-CodeLens hooks."""
    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    original_content = "#!/bin/bash\necho 'original hook'"
    post_commit.write_text(original_content)
    post_commit.chmod(0o755)

    # Try to uninstall without installing first
    await hook_installer.uninstall_hooks(temp_repo)

    assert post_commit.exists()
    assert post_commit.read_text() == original_content


@pytest.mark.asyncio
async def test_reinstall_updates_script_only(
    temp_repo: Path,
    hook_installer: HookInstaller,
) -> None:
    """Reinstalling should update standalone script without duplicating injection."""
    post_commit = temp_repo / ".git" / "hooks" / "post-commit"
    original_content = "#!/bin/bash\necho 'original hook'"
    post_commit.write_text(original_content)
    post_commit.chmod(0o755)

    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=8000,
    )

    # Install again with different port
    await hook_installer.install_hooks(
        temp_repo,
        (HookEvent.POST_COMMIT,),
        port=9000,
    )

    content = post_commit.read_text()
    # Should only have one injection
    assert content.count("# CodeLens Trigger Hook") == 1

    # Standalone script should have new port
    standalone = temp_repo / ".git" / "hooks" / "code-lens-review-hook.sh"
    assert "9000" in standalone.read_text()


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

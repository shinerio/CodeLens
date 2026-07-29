import os
import stat
from pathlib import Path

import pytest

from codelens.plugin.domain.models import HookEvent
from codelens.plugin.trigger.local_hook.hook_installer import HookInstaller


@pytest.fixture
def repository(git_repository: Path) -> Path:
    return git_repository


@pytest.fixture
def hook_installer() -> HookInstaller:
    plugin_dir = (
        Path(__file__).parents[3]
        / "src"
        / "codelens"
        / "plugin"
        / "trigger"
        / "local_hook"
    )
    return HookInstaller(plugin_dir)


async def _reinstall(hook_installer: HookInstaller, repository: Path) -> None:
    await hook_installer.uninstall_hooks(repository)
    await hook_installer.install_hooks(repository, (HookEvent.POST_COMMIT,), 8765)


@pytest.mark.asyncio
async def test_reinstall_recreates_a_missing_hook_without_duplicate_injection(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hook_path = repository / ".git" / "hooks" / "post-commit"

    await _reinstall(hook_installer, repository)
    await _reinstall(hook_installer, repository)

    content = hook_path.read_text(encoding="utf-8")
    assert content.count(HookInstaller.MARKER_COMMENT) == 1
    assert hook_path.stat().st_mode & stat.S_IXUSR


@pytest.mark.asyncio
async def test_reinstall_preserves_an_existing_user_hook_and_makes_it_executable(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hook_path = repository / ".git" / "hooks" / "post-commit"
    user_hook = "#!/bin/sh\necho user-hook\n"
    hook_path.write_text(user_hook, encoding="utf-8")
    hook_path.chmod(0o600)

    await _reinstall(hook_installer, repository)
    await _reinstall(hook_installer, repository)

    content = hook_path.read_text(encoding="utf-8")
    assert "echo user-hook" in content
    assert content.count(HookInstaller.MARKER_COMMENT) == 1
    assert os.access(hook_path, os.X_OK)


@pytest.mark.asyncio
async def test_reinstall_repairs_a_broken_marker_without_deleting_user_code(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hook_path = repository / ".git" / "hooks" / "post-commit"
    hook_path.write_text(
        f"#!/bin/sh\n{HookInstaller.MARKER_COMMENT}\necho user-hook\n",
        encoding="utf-8",
    )

    await _reinstall(hook_installer, repository)

    content = hook_path.read_text(encoding="utf-8")
    assert "echo user-hook" in content
    assert content.count(HookInstaller.MARKER_COMMENT) == 1
    assert "code-lens-review-hook.sh" in content


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks requires extra Windows privileges")
async def test_reinstall_wraps_and_then_restores_an_existing_user_symlink(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hooks_dir = repository / ".git" / "hooks"
    user_hook = hooks_dir / "user-post-commit"
    user_hook.write_text("#!/bin/sh\necho linked-user-hook\n", encoding="utf-8")
    user_hook.chmod(0o700)
    hook_path = hooks_dir / "post-commit"
    hook_path.symlink_to(user_hook.name)

    await hook_installer.install_hooks(repository, (HookEvent.POST_COMMIT,), 8765)

    assert not hook_path.is_symlink()
    assert "code-lens-review-hook.sh" in hook_path.read_text(encoding="utf-8")
    assert (hooks_dir / "post-commit.codelens-user-hook").is_symlink()

    await hook_installer.uninstall_hooks(repository)

    assert hook_path.is_symlink()
    assert hook_path.readlink() == Path(user_hook.name)


@pytest.mark.asyncio
async def test_reinstall_wraps_and_restores_a_non_shell_user_hook(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hooks_dir = repository / ".git" / "hooks"
    hook_path = hooks_dir / "post-commit"
    user_hook = "#!/usr/bin/env python3\nprint('user hook')\n"
    hook_path.write_text(user_hook, encoding="utf-8")
    hook_path.chmod(0o700)

    await _reinstall(hook_installer, repository)

    backup_path = hooks_dir / "post-commit.codelens-user-hook"
    assert backup_path.read_text(encoding="utf-8") == user_hook
    assert "code-lens-review-hook.sh" in hook_path.read_text(encoding="utf-8")

    await hook_installer.uninstall_hooks(repository)

    assert hook_path.read_text(encoding="utf-8") == user_hook
    assert not backup_path.exists()


@pytest.mark.asyncio
async def test_reinstall_replaces_legacy_injection_without_consuming_user_stdin(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hook_path = repository / ".git" / "hooks" / "pre-push"
    legacy_injection = HookInstaller.LEGACY_INJECTION_LINE_TEMPLATE.format(
        script_name=HookInstaller.STANDALONE_SCRIPT_NAME
    )
    hook_path.write_text(
        f"#!/bin/sh\n{HookInstaller.MARKER_COMMENT}\n{legacy_injection}\ncat >/tmp/user-input\n",
        encoding="utf-8",
    )
    hook_path.chmod(0o700)

    await hook_installer.uninstall_hooks(repository)
    await hook_installer.install_hooks(repository, (HookEvent.PRE_PUSH,), 8765)

    content = hook_path.read_text(encoding="utf-8")
    assert legacy_injection not in content
    assert "< /dev/null || true" in content
    assert "cat >/tmp/user-input" in content


@pytest.mark.asyncio
async def test_status_rejects_a_marker_without_a_working_injection(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hooks_dir = repository / ".git" / "hooks"
    standalone = hooks_dir / HookInstaller.STANDALONE_SCRIPT_NAME
    standalone.write_text("#!/bin/sh\n", encoding="utf-8")
    standalone.chmod(0o700)
    hook_path = hooks_dir / "post-commit"
    hook_path.write_text(
        f"#!/bin/sh\n{HookInstaller.MARKER_COMMENT}\necho user-hook\n",
        encoding="utf-8",
    )
    hook_path.chmod(0o700)

    status = await hook_installer.is_installed(repository)

    assert status[HookEvent.POST_COMMIT] is False


@pytest.mark.asyncio
async def test_status_rejects_a_corrupted_standalone_script(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    await hook_installer.install_hooks(repository, (HookEvent.POST_COMMIT,), 8765)
    standalone = (
        repository
        / ".git"
        / "hooks"
        / HookInstaller.STANDALONE_SCRIPT_NAME
    )
    standalone.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    standalone.chmod(0o700)

    status = await hook_installer.is_installed(repository)

    assert status[HookEvent.POST_COMMIT] is False

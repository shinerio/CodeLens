import asyncio
import os
import stat
import subprocess
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
        Path(__file__).parents[3] / "src" / "codelens" / "plugin" / "trigger" / "local_hook"
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
async def test_wrapper_write_failure_leaves_the_original_hook_in_place(
    repository: Path,
    hook_installer: HookInstaller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = repository / ".git" / "hooks"
    hook_path = hooks_dir / "post-commit"
    original = "#!/usr/bin/env python3\nprint('user hook')\n"
    hook_path.write_text(original, encoding="utf-8")
    hook_path.chmod(0o700)

    def fail_to_write(_path: Path, _content: str) -> None:
        raise OSError("simulated wrapper write failure")

    monkeypatch.setattr(hook_installer, "_write_executable_hook", fail_to_write)

    with pytest.raises(OSError, match="simulated wrapper write failure"):
        await hook_installer.install_hooks(repository, (HookEvent.POST_COMMIT,), 8765)

    assert hook_path.read_text(encoding="utf-8") == original
    assert not (hooks_dir / "post-commit.codelens-user-hook").exists()
    assert not tuple(hooks_dir.glob(".post-commit-*.tmp"))


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks requires extra Windows privileges")
async def test_uninstall_preserves_both_hooks_when_the_wrapper_was_replaced(
    repository: Path,
    hook_installer: HookInstaller,
) -> None:
    hooks_dir = repository / ".git" / "hooks"
    original_target = hooks_dir / "original-hook"
    original_target.write_text("#!/bin/sh\n", encoding="utf-8")
    original_target.chmod(0o700)
    hook_path = hooks_dir / "post-commit"
    hook_path.symlink_to(original_target.name)
    await hook_installer.install_hooks(repository, (HookEvent.POST_COMMIT,), 8765)
    backup_path = hooks_dir / "post-commit.codelens-user-hook"

    replacement_target = hooks_dir / "replacement-hook"
    replacement_target.write_text("#!/bin/sh\n", encoding="utf-8")
    replacement_target.chmod(0o700)
    hook_path.unlink()
    hook_path.symlink_to(replacement_target.name)

    with pytest.raises(FileExistsError, match="changed while CodeLens backup exists"):
        await hook_installer.uninstall_hooks(repository)

    assert hook_path.is_symlink()
    assert hook_path.readlink() == Path(replacement_target.name)
    assert backup_path.is_symlink()
    assert backup_path.readlink() == Path(original_target.name)


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
    standalone = repository / ".git" / "hooks" / HookInstaller.STANDALONE_SCRIPT_NAME
    standalone.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    standalone.chmod(0o700)

    status = await hook_installer.is_installed(repository)

    assert status[HookEvent.POST_COMMIT] is False


@pytest.mark.asyncio
async def test_pre_push_input_reaches_codelens_and_the_existing_user_hook(
    repository: Path,
    hook_installer: HookInstaller,
    tmp_path: Path,
) -> None:
    hooks_dir = repository / ".git" / "hooks"
    hook_path = hooks_dir / "pre-push"
    user_capture = tmp_path / "user-input.txt"
    payload_capture = tmp_path / "payload.json"
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    fake_curl = executable_dir / "curl"
    fake_curl.write_text(
        "#!/bin/sh\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-d" ]; then shift; printf \'%s\' "$1" > "$PAYLOAD_CAPTURE"; fi\n'
        "  shift\n"
        "done\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o700)
    hook_path.write_text(
        '#!/bin/sh\ncat > "$USER_CAPTURE"\n',
        encoding="utf-8",
    )
    hook_path.chmod(0o700)
    await hook_installer.install_hooks(repository, (HookEvent.PRE_PUSH,), 8765)
    pushed_ref = (
        "refs/heads/feature 1111111111111111111111111111111111111111 "
        "refs/heads/feature 0000000000000000000000000000000000000000\n"
    )
    environment = {
        **os.environ,
        "PATH": f"{executable_dir}{os.pathsep}{os.environ['PATH']}",
        "PAYLOAD_CAPTURE": str(payload_capture),
        "USER_CAPTURE": str(user_capture),
    }

    completed = await asyncio.to_thread(
        subprocess.run,
        [str(hook_path), "origin", "https://example.invalid/repository.git"],
        cwd=repository,
        input=pushed_ref,
        text=True,
        env=environment,
        check=False,
        timeout=10,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"push_ref":"refs/heads/feature"' in payload_capture.read_text(encoding="utf-8")
    assert user_capture.read_text(encoding="utf-8") == pushed_ref

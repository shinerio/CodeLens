"""Install and uninstall git hook scripts in repositories."""

import asyncio
import os
import shlex
import stat
import tempfile
from pathlib import Path

from codelens.plugin.domain.models import HookEvent


class HookInstaller:
    """Manage git hook script installation and uninstallation in repositories.

    Uses a standalone script approach to avoid overwriting user hooks:
    1. Creates a standalone script: .git/hooks/code-lens-review-hook.sh
    2. If no user hook exists: creates a new hook file that calls the standalone script
    3. If user hook exists: injects a call to standalone script after shebang
    4. On uninstall: removes injected lines and deletes standalone script

    CodeLens-created hooks are regular files for cross-platform compatibility.
    Existing user symlinks are moved beside the wrapper and restored on uninstall.
    """

    HOOK_SCRIPT_TEMPLATE = "hook_script.sh"
    STANDALONE_SCRIPT_NAME = "code-lens-review-hook.sh"
    USER_HOOK_BACKUP_SUFFIX = ".codelens-user-hook"
    MARKER_COMMENT = "# CodeLens Trigger Hook"
    INJECTION_LINE_TEMPLATE = (
        'CODELENS_HOOK_INPUT="$(mktemp "${{TMPDIR:-/tmp}}/codelens-hook.XXXXXX" '
        '2>/dev/null || true)"; if [ -n "$CODELENS_HOOK_INPUT" ]; then '
        'cat > "$CODELENS_HOOK_INPUT"; '
        '"$(cd "$(dirname "$0")" && pwd)/{script_name}" '
        '"$(basename "$0")" "$@" < "$CODELENS_HOOK_INPUT" || true; '
        'exec < "$CODELENS_HOOK_INPUT"; rm -f "$CODELENS_HOOK_INPUT"; '
        'else "$(cd "$(dirname "$0")" && pwd)/{script_name}" '
        '"$(basename "$0")" "$@" < /dev/null || true; fi'
    )
    SHEBANG = "#!/usr/bin/env bash"

    def __init__(self, plugin_dir: Path) -> None:
        """Initialize with the plugin directory containing the hook script template.

        Args:
            plugin_dir: Path to local_hook directory containing hook_script.sh.
        """
        self._plugin_dir = plugin_dir
        self._template_path = plugin_dir / self.HOOK_SCRIPT_TEMPLATE

    async def install_hooks(
        self,
        repository_path: Path,
        events: tuple[HookEvent, ...],
        port: int,
    ) -> None:
        """Install hook scripts for specified events in a repository.

        Args:
            repository_path: Absolute path to the git repository.
            events: Tuple of hook events to install (e.g., post-commit, pre-push).
            port: Port number for the CodeLens API.

        Raises:
            ValueError: If repository_path is not a valid git repository.
            OSError: If hook installation fails.
        """
        git_dir = await self._get_git_dir(repository_path)
        hooks_dir = git_dir / "hooks"
        await asyncio.to_thread(hooks_dir.mkdir, parents=True, exist_ok=True)

        # Write standalone script
        template_content = await asyncio.to_thread(self._template_path.read_text, encoding="utf-8")
        hook_content = template_content.replace("__PORT__", str(port))
        standalone_path = hooks_dir / self.STANDALONE_SCRIPT_NAME
        await asyncio.to_thread(self._write_standalone_script, standalone_path, hook_content)

        # Install each event hook
        for event in events:
            hook_path = hooks_dir / event.value
            await asyncio.to_thread(self._install_single_hook, hook_path, standalone_path)

    async def uninstall_hooks(self, repository_path: Path) -> None:
        """Uninstall all CodeLens hook scripts from a repository.

        Removes injected lines from user hooks and deletes standalone script.

        Args:
            repository_path: Absolute path to the git repository.

        Raises:
            ValueError: If repository_path is not a valid git repository.
        """
        git_dir = await self._get_git_dir(repository_path)
        hooks_dir = git_dir / "hooks"
        standalone_path = hooks_dir / self.STANDALONE_SCRIPT_NAME

        # Remove injection from each event hook
        for event in HookEvent:
            hook_path = hooks_dir / event.value
            await asyncio.to_thread(self._uninstall_single_hook, hook_path)

        # Delete standalone script
        if await asyncio.to_thread(standalone_path.exists):
            await asyncio.to_thread(standalone_path.unlink)

    async def is_installed(self, repository_path: Path) -> dict[HookEvent, bool]:
        """Check which hooks are currently installed in a repository.

        Args:
            repository_path: Absolute path to the git repository.

        Returns:
            Dictionary mapping each HookEvent to its installation status.
        """
        git_dir = await self._get_git_dir(repository_path)
        hooks_dir = git_dir / "hooks"
        standalone_path = hooks_dir / self.STANDALONE_SCRIPT_NAME

        if not await asyncio.to_thread(self._is_valid_standalone_script, standalone_path):
            return {event: False for event in HookEvent}

        result: dict[HookEvent, bool] = {}
        for event in HookEvent:
            hook_path = hooks_dir / event.value
            installed = await asyncio.to_thread(self._is_codelens_hook, hook_path, standalone_path)
            result[event] = installed
        return result

    async def _get_git_dir(self, repository_path: Path) -> Path:
        """Get the .git directory for a repository.

        Args:
            repository_path: Absolute path to the git repository.

        Returns:
            Path to the .git directory.

        Raises:
            ValueError: If repository_path is not a valid git repository.
        """
        git_dir = repository_path / ".git"
        if not await asyncio.to_thread(git_dir.exists):
            raise ValueError(f"Not a git repository: {repository_path}")
        if not await asyncio.to_thread(git_dir.is_dir):
            raise ValueError(f".git is not a directory: {git_dir}")
        return git_dir

    def _write_standalone_script(self, standalone_path: Path, hook_content: str) -> None:
        """Write the standalone CodeLens hook script.

        Args:
            standalone_path: Path where standalone script should be written.
            hook_content: Content of the hook script.
        """
        standalone_path.write_text(hook_content, encoding="utf-8")
        standalone_path.chmod(standalone_path.stat().st_mode | 0o111)

    def _install_single_hook(self, hook_path: Path, standalone_path: Path) -> None:
        """Install a single hook by creating a new file or injecting into existing.

        If no hook exists, creates a new hook file that calls the standalone script.
        Shell hooks receive a call after their shebang. Non-shell hooks and user
        symlinks use a reversible wrapper so their interpreter, contents, permissions,
        and link target can be restored.

        Args:
            hook_path: Path where the hook should be installed.
            standalone_path: Path to the standalone CodeLens script.
        """
        injection_line = self._injection_line()
        full_injection = f"{self.MARKER_COMMENT}\n{injection_line}"

        if hook_path.is_symlink():
            self._wrap_user_hook(hook_path, full_injection)
            return

        if not hook_path.exists():
            self._write_executable_hook(
                hook_path,
                f"{self.SHEBANG}\n{full_injection}\n",
            )
            return

        if self._is_codelens_hook(hook_path, standalone_path):
            self._make_executable(hook_path)
            return

        content = hook_path.read_text(encoding="utf-8")
        lines = self._without_codelens_injection(content.split("\n"))

        if lines and lines[0].startswith("#!") and not self._is_shell_shebang(lines[0]):
            self._wrap_user_hook(hook_path, full_injection)
            return
        if lines and lines[0].startswith("#!"):
            lines.insert(1, full_injection)
        else:
            lines.insert(0, f"{self.SHEBANG}\n{full_injection}")

        hook_path.write_text("\n".join(lines), encoding="utf-8")
        self._make_executable(hook_path)

    def _uninstall_single_hook(self, hook_path: Path) -> None:
        """Uninstall a single hook by removing injection or deleting the file.

        If the hook file only contains CodeLens content (shebang + marker + injection),
        deletes the file entirely. Otherwise, removes only the injected lines.

        Args:
            hook_path: Path to the hook to uninstall.
        """
        backup_path = self._user_hook_backup_path(hook_path)
        if self._path_entry_exists(backup_path):
            if not self._path_entry_exists(hook_path):
                backup_path.rename(hook_path)
                return
            if self._has_codelens_injection(hook_path):
                backup_path.replace(hook_path)
                return
            raise FileExistsError(f"Git hook changed while CodeLens backup exists: {hook_path}")

        if hook_path.is_symlink() or not hook_path.exists():
            return

        # Read and remove injected lines from hook file
        try:
            content = hook_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        lines = self._without_codelens_injection(content.split("\n"))

        # Check if remaining content is only CodeLens boilerplate (shebang + empty)
        remaining = "\n".join(lines).strip()
        if remaining == self.SHEBANG or remaining == "":
            hook_path.unlink()
        else:
            hook_path.write_text("\n".join(lines), encoding="utf-8")

    def _is_codelens_hook(self, hook_path: Path, standalone_path: Path) -> bool:
        """Check if a hook file is a CodeLens trigger hook.

        Args:
            hook_path: Path to the hook file to check.
            standalone_path: Standalone script expected beside the hook.

        Returns:
            True if the hook is a CodeLens trigger hook, False otherwise.
        """
        if (
            hook_path.is_symlink()
            or not self._is_executable_file(hook_path)
            or not self._is_executable_file(standalone_path)
        ):
            return False

        try:
            lines = hook_path.read_text(encoding="utf-8").split("\n")
            injection_line = self._injection_line()
            return any(
                lines[index].strip() == self.MARKER_COMMENT
                and index + 1 < len(lines)
                and lines[index + 1].strip() == injection_line
                for index in range(len(lines))
            )
        except (OSError, UnicodeDecodeError):
            return False

    def _wrap_user_hook(self, hook_path: Path, full_injection: str) -> None:
        """Replace a non-shell user hook with a restorable shell wrapper."""

        backup_path = self._user_hook_backup_path(hook_path)
        if self._path_entry_exists(backup_path):
            raise FileExistsError(f"Git hook backup already exists: {backup_path}")
        original_hook_line = f'"$(cd "$(dirname "$0")" && pwd)/{backup_path.name}" "$@"'
        descriptor, temporary_name = tempfile.mkstemp(
            dir=hook_path.parent,
            prefix=f".{hook_path.name}-",
            suffix=".tmp",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        has_moved_original = False
        try:
            self._write_executable_hook(
                temporary_path,
                f"{self.SHEBANG}\n{full_injection}\n{original_hook_line}\n",
            )
            hook_path.rename(backup_path)
            has_moved_original = True
            temporary_path.replace(hook_path)
        except BaseException:
            # Cancellation must not strand the user's hook under the backup name.
            if (
                has_moved_original
                and not self._path_entry_exists(hook_path)
                and self._path_entry_exists(backup_path)
            ):
                backup_path.rename(hook_path)
            raise
        finally:
            temporary_path.unlink(missing_ok=True)

    def _without_codelens_injection(self, lines: list[str]) -> list[str]:
        """Remove exact CodeLens injection pairs and orphaned marker lines."""

        injection_lines = self._known_injection_lines()
        cleaned: list[str] = []
        index = 0
        while index < len(lines):
            if lines[index].strip() != self.MARKER_COMMENT:
                cleaned.append(lines[index])
                index += 1
                continue
            index += 1
            if index < len(lines) and lines[index].strip() in injection_lines:
                index += 1
        return cleaned

    def _has_codelens_injection(self, hook_path: Path) -> bool:
        if hook_path.is_symlink():
            return False
        try:
            lines = hook_path.read_text(encoding="utf-8").split("\n")
            injection_lines = self._known_injection_lines()
            return any(
                lines[index].strip() == self.MARKER_COMMENT
                and index + 1 < len(lines)
                and lines[index + 1].strip() in injection_lines
                for index in range(len(lines))
            )
        except (OSError, UnicodeDecodeError):
            return False

    def _injection_line(self) -> str:
        return self.INJECTION_LINE_TEMPLATE.format(script_name=self.STANDALONE_SCRIPT_NAME)

    def _known_injection_lines(self) -> set[str]:
        return {self._injection_line()}

    @staticmethod
    def _is_shell_shebang(line: str) -> bool:
        try:
            arguments = shlex.split(line[2:].strip())
        except ValueError:
            return False
        if not arguments:
            return False
        interpreter = Path(arguments[0]).name
        if interpreter == "env":
            commands = [argument for argument in arguments[1:] if not argument.startswith("-")]
            if not commands:
                return False
            interpreter = Path(commands[0]).name
        return interpreter in {"ash", "bash", "dash", "ksh", "sh", "zsh"}

    def _user_hook_backup_path(self, hook_path: Path) -> Path:
        return hook_path.with_name(f"{hook_path.name}{self.USER_HOOK_BACKUP_SUFFIX}")

    @staticmethod
    def _path_entry_exists(path: Path) -> bool:
        return path.exists() or path.is_symlink()

    @staticmethod
    def _is_executable_file(path: Path) -> bool:
        try:
            return path.is_file() and bool(path.stat().st_mode & stat.S_IXUSR)
        except OSError:
            return False

    @classmethod
    def _is_valid_standalone_script(cls, path: Path) -> bool:
        if not cls._is_executable_file(path):
            return False
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        return (
            "CODELENS_API=" in content
            and "/api/trigger-events" in content
            and "__PORT__" not in content
        )

    @staticmethod
    def _make_executable(path: Path) -> None:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _write_executable_hook(self, hook_path: Path, content: str) -> None:
        hook_path.write_text(content, encoding="utf-8")
        self._make_executable(hook_path)

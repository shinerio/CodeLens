"""Install and uninstall git hook scripts in repositories."""

import asyncio
import shutil
from pathlib import Path

from codelens.trigger.domain.models import HookEvent


class HookInstaller:
    """Manage git hook script installation and uninstallation in repositories.

    Installs CodeLens trigger hooks (post-commit, pre-push) into .git/hooks/
    directories. Backs up existing hooks to preserve user customizations.
    """

    HOOK_SCRIPT_TEMPLATE = "hook_script.sh"
    BACKUP_SUFFIX = ".codelens-backup"
    MARKER_COMMENT = "# CodeLens Trigger Hook"

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

        template_content = await asyncio.to_thread(
            self._template_path.read_text, encoding="utf-8"
        )
        hook_content = template_content.replace("__PORT__", str(port))

        for event in events:
            hook_path = hooks_dir / event.value
            await asyncio.to_thread(
                self._install_single_hook, hook_path, hook_content
            )

    async def uninstall_hooks(self, repository_path: Path) -> None:
        """Uninstall all CodeLens hook scripts from a repository.

        Restores backed-up hooks if they exist.

        Args:
            repository_path: Absolute path to the git repository.

        Raises:
            ValueError: If repository_path is not a valid git repository.
        """
        git_dir = await self._get_git_dir(repository_path)
        hooks_dir = git_dir / "hooks"

        for event in HookEvent:
            hook_path = hooks_dir / event.value
            backup_path = hooks_dir / f"{event.value}{self.BACKUP_SUFFIX}"
            await asyncio.to_thread(
                self._uninstall_single_hook, hook_path, backup_path
            )

    async def is_installed(self, repository_path: Path) -> dict[HookEvent, bool]:
        """Check which hooks are currently installed in a repository.

        Args:
            repository_path: Absolute path to the git repository.

        Returns:
            Dictionary mapping each HookEvent to its installation status.
        """
        git_dir = await self._get_git_dir(repository_path)
        hooks_dir = git_dir / "hooks"

        result: dict[HookEvent, bool] = {}
        for event in HookEvent:
            hook_path = hooks_dir / event.value
            installed = await asyncio.to_thread(self._is_codelens_hook, hook_path)
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

    def _install_single_hook(self, hook_path: Path, hook_content: str) -> None:
        """Install a single hook script, backing up existing hooks if needed.

        Args:
            hook_path: Path where the hook should be installed.
            hook_content: Content of the hook script to install.
        """
        backup_path = hook_path.with_name(hook_path.name + self.BACKUP_SUFFIX)

        # If hook exists and is not already a CodeLens hook, back it up
        if hook_path.exists():
            existing_content = hook_path.read_text(encoding="utf-8")
            if self.MARKER_COMMENT not in existing_content:
                # Not a CodeLens hook, back it up
                if not backup_path.exists():
                    shutil.copy2(hook_path, backup_path)
            # If it's already a CodeLens hook, skip backup to avoid duplicating backups

        # Write the new hook
        hook_path.write_text(hook_content, encoding="utf-8")
        hook_path.chmod(hook_path.stat().st_mode | 0o111)  # Make executable

    def _uninstall_single_hook(self, hook_path: Path, backup_path: Path) -> None:
        """Uninstall a single hook script, restoring backup if it exists.

        Args:
            hook_path: Path to the hook to uninstall.
            backup_path: Path to the backup hook to restore.
        """
        if not hook_path.exists():
            return

        # Only uninstall if it's a CodeLens hook
        if not self._is_codelens_hook(hook_path):
            return

        # Remove the CodeLens hook
        hook_path.unlink()

        # Restore backup if it exists
        if backup_path.exists():
            shutil.move(str(backup_path), str(hook_path))

    def _is_codelens_hook(self, hook_path: Path) -> bool:
        """Check if a hook file is a CodeLens trigger hook.

        Args:
            hook_path: Path to the hook file to check.

        Returns:
            True if the hook is a CodeLens trigger hook, False otherwise.
        """
        if not hook_path.exists():
            return False
        if not hook_path.is_file():
            return False

        try:
            content = hook_path.read_text(encoding="utf-8")
            return self.MARKER_COMMENT in content
        except (OSError, UnicodeDecodeError):
            return False

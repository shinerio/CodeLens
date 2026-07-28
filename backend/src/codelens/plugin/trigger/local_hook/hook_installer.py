"""Install and uninstall git hook scripts in repositories."""

import asyncio
import stat
from pathlib import Path

from codelens.trigger.domain.models import HookEvent


class HookInstaller:
    """Manage git hook script installation and uninstallation in repositories.

    Uses a standalone script approach to avoid overwriting user hooks:
    1. Creates a standalone script: .git/hooks/code-lens-review-hook.sh
    2. If no user hook exists: creates a new hook file that calls the standalone script
    3. If user hook exists: injects a call to standalone script after shebang
    4. On uninstall: removes injected lines and deletes standalone script

    Symlinks are not used to ensure cross-platform compatibility (Windows).
    """

    HOOK_SCRIPT_TEMPLATE = "hook_script.sh"
    STANDALONE_SCRIPT_NAME = "code-lens-review-hook.sh"
    MARKER_COMMENT = "# CodeLens Trigger Hook"
    INJECTION_LINE_TEMPLATE = '"$GIT_DIR/hooks/{script_name}" "$@" || true'
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
        template_content = await asyncio.to_thread(
            self._template_path.read_text, encoding="utf-8"
        )
        hook_content = template_content.replace("__PORT__", str(port))
        standalone_path = hooks_dir / self.STANDALONE_SCRIPT_NAME
        await asyncio.to_thread(
            self._write_standalone_script, standalone_path, hook_content
        )

        # Install each event hook
        for event in events:
            hook_path = hooks_dir / event.value
            await asyncio.to_thread(
                self._install_single_hook, hook_path, standalone_path
            )

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
        if standalone_path.exists():
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

        # Check if standalone script exists
        if not standalone_path.exists():
            return {event: False for event in HookEvent}

        result: dict[HookEvent, bool] = {}
        for event in HookEvent:
            hook_path = hooks_dir / event.value
            installed = await asyncio.to_thread(
                self._is_codelens_hook, hook_path, standalone_path
            )
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
        If a user hook exists, injects a call to the standalone script after shebang.
        If an old symlink exists (from previous version), replaces it with a regular file.
        Does not use symlinks for cross-platform compatibility.

        Args:
            hook_path: Path where the hook should be installed.
            standalone_path: Path to the standalone CodeLens script.
        """
        injection_line = self.INJECTION_LINE_TEMPLATE.format(
            script_name=self.STANDALONE_SCRIPT_NAME
        )
        full_injection = f"{self.MARKER_COMMENT}\n{injection_line}"

        # If hook is a symlink (from old version), remove it and create a regular file
        if hook_path.is_symlink():
            hook_path.unlink()
            content = f"{self.SHEBANG}\n{full_injection}\n"
            hook_path.write_text(content, encoding="utf-8")
            hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return

        # If hook doesn't exist, create a new hook file
        if not hook_path.exists():
            content = f"{self.SHEBANG}\n{full_injection}\n"
            hook_path.write_text(content, encoding="utf-8")
            # Make executable
            hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return

        # If it's already a CodeLens hook, skip (idempotent)
        if self._is_codelens_hook(hook_path, standalone_path):
            return

        # User hook exists, inject call after shebang
        content = hook_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # Find shebang line
        if lines and lines[0].startswith("#!"):
            # Inject after shebang
            lines.insert(1, full_injection)
        else:
            # No shebang, add at beginning
            lines.insert(0, full_injection)

        hook_path.write_text("\n".join(lines), encoding="utf-8")

    def _uninstall_single_hook(self, hook_path: Path) -> None:
        """Uninstall a single hook by removing injection or deleting the file.

        If the hook file only contains CodeLens content (shebang + marker + injection),
        deletes the file entirely. Otherwise, removes only the injected lines.

        Args:
            hook_path: Path to the hook to uninstall.
        """
        if not hook_path.exists():
            return

        # Read and remove injected lines from hook file
        try:
            content = hook_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        lines = content.split("\n")
        # Find and remove marker comment and injection line
        i = 0
        while i < len(lines) - 1:
            if lines[i].strip() == self.MARKER_COMMENT:
                # Remove marker and next line (injection)
                del lines[i : i + 2]
            else:
                i += 1

        # Check if remaining content is only CodeLens boilerplate (shebang + empty)
        remaining = "\n".join(lines).strip()
        if remaining == self.SHEBANG or remaining == "":
            # Pure CodeLens hook - delete the file
            hook_path.unlink()
        else:
            # User hook with injection removed - write back
            hook_path.write_text("\n".join(lines), encoding="utf-8")

    def _is_codelens_hook(self, hook_path: Path, standalone_path: Path) -> bool:
        """Check if a hook file is a CodeLens trigger hook.

        Args:
            hook_path: Path to the hook file to check.
            standalone_path: Unused, kept for signature compatibility.

        Returns:
            True if the hook is a CodeLens trigger hook, False otherwise.
        """
        if not hook_path.exists() or not hook_path.is_file():
            return False

        try:
            content = hook_path.read_text(encoding="utf-8")
            return self.MARKER_COMMENT in content
        except (OSError, UnicodeDecodeError):
            return False

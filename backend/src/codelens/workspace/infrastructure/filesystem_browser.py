import asyncio
import os
import string
from pathlib import Path

from codelens.shared.domain.errors import FilesystemBrowseError
from codelens.workspace.domain.ports import DirectoryEntry, DirectoryListing


class LocalFilesystemBrowserAdapter:
    """Browse every platform filesystem root using bounded directory-only listings."""

    def __init__(self, *, max_directories: int = 2000) -> None:
        if max_directories < 1:
            raise ValueError("directory listing limit must be positive")
        self._max_directories = max_directories

    async def browse(self, path: Path | None) -> DirectoryListing:
        """Isolate blocking root discovery and directory iteration from the event loop."""

        return await asyncio.to_thread(self._browse_sync, path)

    def _browse_sync(self, path: Path | None) -> DirectoryListing:
        roots = self._roots()
        if path is None:
            return DirectoryListing(None, None, roots, ())
        try:
            current = path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise FilesystemBrowseError("directory does not exist or cannot be resolved") from None
        if not current.is_dir():
            raise FilesystemBrowseError("selected path is not a directory")
        if not self._can_browse(current):
            raise FilesystemBrowseError("directory cannot be read")
        entries: list[DirectoryEntry] = []
        is_truncated = False
        try:
            with os.scandir(current) as iterator:
                for raw_entry in iterator:
                    try:
                        if not raw_entry.is_dir(follow_symlinks=True):
                            continue
                        entry_path = Path(raw_entry.path).resolve(strict=True)
                        if not self._can_browse(entry_path):
                            continue
                    except (OSError, RuntimeError):
                        continue
                    entries.append(
                        DirectoryEntry(
                            name=raw_entry.name,
                            path=entry_path,
                            is_git_repository=self._has_git_marker(entry_path),
                        )
                    )
                    if len(entries) > self._max_directories:
                        is_truncated = True
                        break
        except OSError:
            raise FilesystemBrowseError("directory cannot be read") from None
        entries.sort(key=lambda entry: (not entry.is_git_repository, entry.name.casefold()))
        parent = None if current.parent == current else current.parent
        return DirectoryListing(
            current_path=current,
            parent_path=parent,
            roots=roots,
            directories=tuple(entries[: self._max_directories]),
            current_is_git_repository=self._has_git_marker(current),
            is_truncated=is_truncated,
        )

    @staticmethod
    def _roots() -> tuple[Path, ...]:
        if os.name != "nt":
            return (Path("/"),)
        return tuple(
            root
            for letter in string.ascii_uppercase
            if (root := Path(f"{letter}:\\")).exists()
        )

    @staticmethod
    def _has_git_marker(path: Path) -> bool:
        try:
            return (path / ".git").exists() or (
                (path / "HEAD").is_file() and (path / "objects").is_dir()
            )
        except (OSError, RuntimeError):
            return False

    @staticmethod
    def _can_browse(path: Path) -> bool:
        """Require the launch user to be able to read and traverse a directory."""

        try:
            return os.access(path, os.R_OK | os.X_OK)
        except OSError:
            return False

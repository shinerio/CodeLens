import asyncio
import logging
from pathlib import Path

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.ports import RepositoryInfo, RepositoryMetadataPort

_LOGGER = logging.getLogger(__name__)


def _resolve_path(path: Path) -> Path:
    return path.expanduser().resolve()


class RepositoryInspector:
    """Validate repository containment before requesting adapter metadata."""

    def __init__(
        self,
        metadata: RepositoryMetadataPort,
        repository_roots: tuple[Path, ...],
    ) -> None:
        self._metadata = metadata
        self._roots = tuple(root.expanduser().resolve() for root in repository_roots)
        _LOGGER.info("RepositoryInspector initialized with %d root(s)", len(self._roots))

    def add_root(self, root: Path | str) -> None:
        """Dynamically add a trusted repository root (e.g., plugin install path)."""
        resolved = Path(root).expanduser().resolve()
        if resolved not in self._roots:
            self._roots = (*self._roots, resolved)
            _LOGGER.info("Added trusted root: %s", resolved)

    async def inspect(self, path: Path) -> RepositoryInfo:
        """Inspect one exact repository root inside configured access boundaries."""
        repository = await asyncio.to_thread(_resolve_path, path)
        if self._roots and not any(repository.is_relative_to(root) for root in self._roots):
            _LOGGER.warning("Repository %s is outside configured roots", repository)
            raise InvalidRepositoryError("repository is outside configured repository roots")
        if not repository.is_dir():
            _LOGGER.debug("Repository path does not exist or is not a directory: %s", repository)
            raise InvalidRepositoryError("repository directory does not exist")
        return await self._metadata.inspect(repository)

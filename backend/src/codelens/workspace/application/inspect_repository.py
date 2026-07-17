import asyncio
from pathlib import Path

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.ports import RepositoryInfo, RepositoryMetadataPort


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

    async def inspect(self, path: Path) -> RepositoryInfo:
        """Inspect one exact repository root inside configured access boundaries."""

        repository = await asyncio.to_thread(_resolve_path, path)
        if self._roots and not any(repository.is_relative_to(root) for root in self._roots):
            raise InvalidRepositoryError("repository is outside configured repository roots")
        if not repository.is_dir():
            raise InvalidRepositoryError("repository directory does not exist")
        return await self._metadata.inspect(repository)

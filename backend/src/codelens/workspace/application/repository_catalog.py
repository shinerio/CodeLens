from pathlib import Path

from codelens.workspace.application.inspect_repository import RepositoryInspector
from codelens.workspace.domain.ports import RepositoryCatalog, RepositoryCatalogPort


class RepositoryCatalogService:
    """Validate an unrestricted local repository before exposing selectable Git refs."""

    def __init__(
        self,
        inspector: RepositoryInspector,
        catalog: RepositoryCatalogPort,
    ) -> None:
        self._inspector = inspector
        self._catalog = catalog

    async def handle(
        self,
        path: Path,
        *,
        commit_offset: int,
        commit_limit: int,
    ) -> RepositoryCatalog:
        """Return refs only after exact Git-root validation succeeds."""

        repository = await self._inspector.inspect(path)
        return await self._catalog.list_catalog(
            repository.path,
            commit_offset=commit_offset,
            commit_limit=commit_limit,
        )

from pathlib import Path

from codelens.workspace.domain.ports import DirectoryBrowserPort, DirectoryListing


class BrowseDirectoriesService:
    """Expose the local directory navigator through an injected filesystem port."""

    def __init__(self, browser: DirectoryBrowserPort) -> None:
        self._browser = browser

    async def handle(self, path: Path | None) -> DirectoryListing:
        """Return platform roots or one bounded directory listing."""

        return await self._browser.browse(path)

"""Application access to the configured Review file exclusion policy."""

import asyncio
from typing import Protocol

from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy


class ReviewFileExclusionPolicySourcePort(Protocol):
    """Load the operator-managed policy applied to newly created Reviews."""

    def get_policy(self) -> ReviewFileExclusionPolicy: ...


class ReviewFileExclusionPolicyStorePort(ReviewFileExclusionPolicySourcePort, Protocol):
    """Persist the Web-managed policy overlay."""

    def save_policy(self, policy: ReviewFileExclusionPolicy) -> None: ...


class ReviewFileExclusionPolicyProviderPort(Protocol):
    """Provide the current policy to Review creation use cases."""

    async def get(self) -> ReviewFileExclusionPolicy: ...


class FileExclusionPolicyService:
    """Manage a Web overlay and merge it with live operator configuration."""

    def __init__(
        self,
        source: ReviewFileExclusionPolicySourcePort,
        web_store: ReviewFileExclusionPolicyStorePort,
    ) -> None:
        self._source = source
        self._web_store = web_store

    async def get(self) -> ReviewFileExclusionPolicy:
        """Load the policy without blocking the request event loop."""

        configured, web = await asyncio.gather(
            asyncio.to_thread(self._source.get_policy),
            asyncio.to_thread(self._web_store.get_policy),
        )
        return self._merge(configured, web)

    async def get_web(self) -> ReviewFileExclusionPolicy:
        """Return only the Web-managed overlay shown in the settings page."""

        return await asyncio.to_thread(self._web_store.get_policy)

    async def update_web(
        self,
        *,
        suffixes: tuple[str, ...] | None = None,
        path_regexes: tuple[str, ...] | None = None,
    ) -> ReviewFileExclusionPolicy:
        """Validate and atomically persist a partial Web overlay update."""

        current = await self.get_web()
        policy = ReviewFileExclusionPolicy(
            suffixes=current.suffixes if suffixes is None else suffixes,
            path_regexes=current.path_regexes if path_regexes is None else path_regexes,
        )
        configured = await asyncio.to_thread(self._source.get_policy)
        self._merge(configured, policy)
        await asyncio.to_thread(self._web_store.save_policy, policy)
        return policy

    @staticmethod
    def _merge(
        configured: ReviewFileExclusionPolicy,
        web: ReviewFileExclusionPolicy,
    ) -> ReviewFileExclusionPolicy:
        """Build and validate the normalized effective policy."""

        return ReviewFileExclusionPolicy(
            suffixes=tuple(sorted(set(configured.suffixes) | set(web.suffixes))),
            path_regexes=tuple(
                sorted(set(configured.path_regexes) | set(web.path_regexes))
            ),
        )

"""Application service for the Review file exclusion policy."""

import asyncio
from typing import Protocol

from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy


class ReviewFileExclusionPolicyStorePort(Protocol):
    """Persist the policy applied when new Review tasks are created."""

    def get_policy(self) -> ReviewFileExclusionPolicy: ...

    def save_policy(self, policy: ReviewFileExclusionPolicy) -> None: ...


class FileExclusionSettingsService:
    """Validate partial settings updates and persist one canonical policy."""

    def __init__(self, store: ReviewFileExclusionPolicyStorePort) -> None:
        self._store = store

    async def get(self) -> ReviewFileExclusionPolicy:
        return await asyncio.to_thread(self._store.get_policy)

    async def update(
        self,
        *,
        suffixes: tuple[str, ...] | None = None,
        path_regexes: tuple[str, ...] | None = None,
        exclude_binary: bool | None = None,
    ) -> ReviewFileExclusionPolicy:
        current = await self.get()
        policy = ReviewFileExclusionPolicy(
            suffixes=current.suffixes if suffixes is None else suffixes,
            path_regexes=(current.path_regexes if path_regexes is None else path_regexes),
            exclude_binary=(current.exclude_binary if exclude_binary is None else exclude_binary),
        )
        await asyncio.to_thread(self._store.save_policy, policy)
        return policy

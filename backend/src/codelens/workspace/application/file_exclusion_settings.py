"""Application access to the configured Review file exclusion policy."""

import asyncio
from typing import Protocol

from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy


class ReviewFileExclusionPolicySourcePort(Protocol):
    """Load the operator-managed policy applied to newly created Reviews."""

    def get_policy(self) -> ReviewFileExclusionPolicy: ...


class ReviewFileExclusionPolicyProviderPort(Protocol):
    """Provide the current policy to Review creation use cases."""

    async def get(self) -> ReviewFileExclusionPolicy: ...


class FileExclusionPolicyService:
    """Read validated configuration outside the event loop for each new Review."""

    def __init__(self, source: ReviewFileExclusionPolicySourcePort) -> None:
        self._source = source

    async def get(self) -> ReviewFileExclusionPolicy:
        """Load the policy without blocking the request event loop."""

        return await asyncio.to_thread(self._source.get_policy)

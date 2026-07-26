"""Application service for Review completion retry settings."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

DEFAULT_MAX_INCOMPLETE_REVIEW_RETRIES = 3
MIN_MAX_INCOMPLETE_REVIEW_RETRIES = 0
MAX_MAX_INCOMPLETE_REVIEW_RETRIES = 20


@dataclass(frozen=True)
class ReviewCompletionSettings:
    """Control how many incomplete finalization attempts are returned to the model."""

    max_incomplete_review_retries: int = DEFAULT_MAX_INCOMPLETE_REVIEW_RETRIES

    def __post_init__(self) -> None:
        value = self.max_incomplete_review_retries
        if (
            isinstance(value, bool)
            or value < MIN_MAX_INCOMPLETE_REVIEW_RETRIES
            or value > MAX_MAX_INCOMPLETE_REVIEW_RETRIES
        ):
            raise ValueError("max incomplete review retries must be between 0 and 20")


class ReviewCompletionSettingsStorePort(Protocol):
    """Persist and provide the completion policy used by subsequent Agent runs."""

    def get_settings(self) -> ReviewCompletionSettings:
        """Load persisted settings or product defaults."""

        raise NotImplementedError

    def save_settings(self, settings: ReviewCompletionSettings) -> None:
        """Atomically replace the persisted settings."""

        raise NotImplementedError


class ReviewCompletionSettingsService:
    """Validate and persist Review completion settings outside the event loop."""

    def __init__(self, store: ReviewCompletionSettingsStorePort) -> None:
        self._store = store

    async def get(self) -> ReviewCompletionSettings:
        """Return the policy used when a new Agent run starts."""

        return await asyncio.to_thread(self._store.get_settings)

    async def update(self, *, max_incomplete_review_retries: int) -> ReviewCompletionSettings:
        """Validate and atomically persist a replacement retry limit."""

        settings = ReviewCompletionSettings(
            max_incomplete_review_retries=max_incomplete_review_retries
        )
        await asyncio.to_thread(self._store.save_settings, settings)
        return settings

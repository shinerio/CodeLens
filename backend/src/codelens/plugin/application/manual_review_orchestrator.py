"""Orchestrate manual-review creation from external source URLs.

When a user provides an external URL (e.g. a CodeHub MR URL), this
orchestrator finds the matching plugin, loads its ``ManualReviewSourcePort``
implementation, and delegates review creation. The plugin is responsible for
parsing the URL, resolving repository metadata, cloning, and calling
``ReviewCreatorPort.create_review_from_trigger`` with the appropriate
``external_context`` so that auto-export routing works on completion.
"""

import logging
from pathlib import Path

from codelens.plugin.domain.models import PluginRecord
from codelens.plugin.domain.ports import (
    ManualReviewSourcePort,
    PluginStorePort,
    ReviewCreatorPort,
    TriggerPluginLoaderPort,
)

_LOGGER = logging.getLogger("codelens.plugin.manual_review.orchestrator")


class ManualReviewRequestError(ValueError):
    """Raised when a manual-review request cannot be dispatched."""


class ManualReviewOrchestrator:
    """Orchestrate user-initiated review creation from external URLs.

    Responsibilities:
    - Validate the plugin exists and has manual_review enabled
    - Load the plugin's ``ManualReviewSourcePort`` implementation
    - Delegate URL resolution and review creation to the plugin
    - Translate failures into structured errors for the API layer
    """

    def __init__(
        self,
        store: PluginStorePort,
        review_creator: ReviewCreatorPort,
        plugin_loader: TriggerPluginLoaderPort,
    ) -> None:
        """Initialize the manual-review orchestrator.

        Args:
            store: Port for querying plugin state.
            review_creator: Port for creating reviews (injected into sources).
            plugin_loader: Port for loading plugin implementations.
        """
        self._store = store
        self._review_creator = review_creator
        self._plugin_loader = plugin_loader

    async def create_review(
        self,
        plugin_id: str,
        source_url: str,
    ) -> str:
        """Create a review from an external source URL via a plugin.

        Args:
            plugin_id: The plugin to use for URL resolution.
            source_url: The external URL (e.g. CodeHub MR URL).

        Returns:
            The created task_id.

        Raises:
            ManualReviewRequestError: If the plugin is not found, manual_review
                is not enabled, the URL is invalid, or the plugin declines.
        """
        record = await self._store.get_plugin(plugin_id)
        if record is None:
            raise ManualReviewRequestError(f"Plugin '{plugin_id}' not found")

        if not record.manual_review_enabled:
            raise ManualReviewRequestError(
                f"Plugin '{plugin_id}' manual_review capability is not enabled"
            )

        if not source_url or len(source_url) > 2048:
            raise ManualReviewRequestError("source_url must be 1-2048 characters")

        source = self._load_source(record)
        try:
            task_id = await source.create_review_from_url(
                source_url=source_url,
                config=record.manual_review_config,
            )
        except Exception:
            _LOGGER.exception(
                "Plugin %s failed to create review from URL",
                plugin_id,
            )
            raise ManualReviewRequestError(
                f"Plugin '{plugin_id}' failed to create review"
            ) from None

        if task_id is None:
            raise ManualReviewRequestError(
                f"Plugin '{plugin_id}' declined to create a review from the given URL"
            )

        _LOGGER.info(
            "Plugin %s created review %s from URL",
            plugin_id,
            task_id,
        )
        return task_id

    def _load_source(self, record: PluginRecord) -> ManualReviewSourcePort:
        """Load the manual-review source for a plugin record.

        Args:
            record: The plugin record to load.

        Returns:
            Instantiated source implementing ``ManualReviewSourcePort``.
        """
        install_path = Path(record.install_path) if record.install_path else None
        return self._plugin_loader.load_source(
            record.plugin_id,
            self._review_creator,
            manifest=record.manifest,
            install_path=install_path,
        )

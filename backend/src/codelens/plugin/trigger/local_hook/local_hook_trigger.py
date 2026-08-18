"""Built-in local git hook trigger plugin implementation."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from codelens.plugin.api.v2 import TriggerReviewPolicy
from codelens.plugin.domain.models import HookEvent
from codelens.plugin.domain.ports import (
    ReviewCreatorPort,
    TriggerSinkPort,
)

_LOGGER = logging.getLogger("codelens.plugin.trigger.local_hook")


class LocalHookTriggerAdapter(TriggerSinkPort):
    """Trigger reviews from local git hook events (post-commit, pre-push).

    Implements debouncing to prevent duplicate review creation when multiple
    events occur in rapid succession (e.g., amend commits, rebases).
    """

    TRIGGER_ID = "local"
    DISPLAY_NAME = "Local Git Hook Trigger"

    def __init__(self, review_creator: ReviewCreatorPort) -> None:
        """Initialize with a review creator port.

        Args:
            review_creator: Port for creating reviews (injected by bootstrap).
        """
        self._review_creator = review_creator
        self._last_trigger: dict[tuple[str, str], datetime] = {}

    @property
    def trigger_id(self) -> str:
        return self.TRIGGER_ID

    @property
    def display_name(self) -> str:
        return self.DISPLAY_NAME

    async def handle_event(
        self,
        event: HookEvent,
        repository_path: Path,
        config: dict[str, Any],
        event_payload: dict[str, Any],
        external_context: dict[str, Any] | None = None,
    ) -> str | None:
        """Handle a git hook event and create a review if appropriate.

        Args:
            event: The git hook event type (post-commit or pre-push).
            repository_path: Absolute path to the repository.
            config: Trigger configuration dict from plugin record.
            event_payload: Event-specific data (commit_sha or push_ref).
            external_context: Platform routing context (None for local triggers).

        Returns:
            Task ID if a review was created, None if debounced or filtered.
        """

        # Check if this event type is enabled in config
        events = [HookEvent(e) for e in config.get("events", [])]
        if event not in events:
            _LOGGER.debug(
                "Event %s not in configured events %s, skipping",
                event.value,
                [e.value for e in events],
            )
            return None

        # Apply debouncing
        debounce_seconds = config.get("debounce_seconds", 0)
        if debounce_seconds > 0:
            if self._is_debounced(repository_path, event, debounce_seconds):
                _LOGGER.info(
                    "Event %s for %s debounced (within %d seconds)",
                    event.value,
                    repository_path,
                    debounce_seconds,
                )
                return None

        # Build scope parameters based on event type
        scope_params = self._build_scope_params(event, event_payload, config)

        # Create the review
        try:
            task_id = await self._review_creator.create_review_from_trigger(
                repository_path=repository_path,
                scope_type=config.get("scope_type", "uncommitted"),
                scope_params=scope_params,
                review_policy=TriggerReviewPolicy.from_config(config),
                external_context=None,
            )
            _LOGGER.info(
                "Created review %s for %s event on %s",
                task_id,
                event.value,
                repository_path,
            )
            return task_id
        except Exception:
            _LOGGER.exception(
                "Failed to create review for %s event on %s",
                event.value,
                repository_path,
            )
            return None

    def _is_debounced(
        self,
        repository_path: Path,
        event: HookEvent,
        debounce_seconds: int,
    ) -> bool:
        """Check if an event should be debounced based on recent triggers.

        Args:
            repository_path: Repository path.
            event: Event type.
            debounce_seconds: Minimum seconds between triggers.

        Returns:
            True if the event should be debounced, False otherwise.
        """
        key = (str(repository_path), event.value)
        now = datetime.now()
        last_trigger = self._last_trigger.get(key)

        if last_trigger is not None:
            elapsed = (now - last_trigger).total_seconds()
            if elapsed < debounce_seconds:
                return True

        self._last_trigger[key] = now
        return False

    def _build_scope_params(
        self,
        event: HookEvent,
        event_payload: dict[str, str],
        config: dict[str, Any],
    ) -> dict[str, str | None]:
        """Build scope parameters for review creation based on scope_type.

        The parameter keys must match what ReviewCreatorAdapter._build_scope expects
        for the configured scope_type, not the event type.

        Args:
            event: The git hook event type.
            event_payload: Event-specific data from the hook script.
            config: Plugin trigger configuration dict.

        Returns:
            Dictionary of scope parameters for the review creator.
        """
        scope_type = config.get("scope_type", "uncommitted")
        # Generate keys based on scope_type, not event type
        if scope_type == "commit":
            # Commit scope expects base_commit and target_ref
            commit_sha = event_payload.get("commit_sha")
            return {
                "base_commit": f"{commit_sha}~1" if commit_sha else None,
                "target_ref": commit_sha,
            }
        elif scope_type == "branch":
            # Branch scope expects base_ref and target_ref
            push_ref = event_payload.get("push_ref")
            return {
                "base_ref": config.get("base_ref"),
                "target_ref": config.get("target_ref") or push_ref,
            }
        else:
            # Uncommitted scope needs no parameters
            return {}

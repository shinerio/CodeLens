"""Built-in local git hook trigger plugin implementation."""

import logging
from datetime import datetime
from pathlib import Path

from codelens.trigger.domain.models import (
    HookEvent,
    TriggerConfig,
)
from codelens.trigger.domain.ports import (
    ReviewCreatorPort,
    TriggerSinkPort,
)

_LOGGER = logging.getLogger("codelens.plugin.trigger.local_hook")


class LocalHookTriggerAdapter(TriggerSinkPort):
    """Trigger reviews from local git hook events (post-commit, pre-push).

    Implements debouncing to prevent duplicate review creation when multiple
    events occur in rapid succession (e.g., amend commits, rebases).
    """

    TRIGGER_ID = "local-git-hook"
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
        config: TriggerConfig,
        event_payload: dict[str, str],
    ) -> str | None:
        """Handle a git hook event and create a review if appropriate.

        Args:
            event: The git hook event type (post-commit or pre-push).
            repository_path: Absolute path to the repository.
            config: Plugin configuration with scope and agent settings.
            event_payload: Event-specific data (commit_sha or push_ref).

        Returns:
            Task ID if a review was created, None if debounced or filtered.
        """
        # Check if this event type is enabled in config
        if event not in config.events:
            _LOGGER.debug(
                "Event %s not in configured events %s, skipping",
                event.value,
                [e.value for e in config.events],
            )
            return None

        # Apply debouncing
        if config.debounce_seconds > 0:
            if self._is_debounced(repository_path, event, config.debounce_seconds):
                _LOGGER.info(
                    "Event %s for %s debounced (within %d seconds)",
                    event.value,
                    repository_path,
                    config.debounce_seconds,
                )
                return None

        # Build scope parameters based on event type
        scope_params = self._build_scope_params(event, event_payload, config)

        # Create the review
        try:
            task_id = await self._review_creator.create_review_from_trigger(
                repository_path=repository_path,
                scope_type=config.scope_type,
                scope_params=scope_params,
                selected_agents=config.selected_agents,
                prompt_locale=config.prompt_locale,
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
        config: TriggerConfig,
    ) -> dict[str, str | None]:
        """Build scope parameters for review creation based on scope_type.

        The parameter keys must match what ReviewCreatorAdapter._build_scope expects
        for the configured scope_type, not the event type.

        Args:
            event: The git hook event type.
            event_payload: Event-specific data from the hook script.
            config: Plugin configuration.

        Returns:
            Dictionary of scope parameters for the review creator.
        """
        # Generate keys based on scope_type, not event type
        if config.scope_type == "commit":
            # Commit scope expects base_commit and target_ref
            commit_sha = event_payload.get("commit_sha")
            return {
                "base_commit": f"{commit_sha}~1" if commit_sha else None,
                "target_ref": commit_sha,
            }
        elif config.scope_type == "branch":
            # Branch scope expects base_ref and target_ref
            push_ref = event_payload.get("push_ref")
            return {
                "base_ref": config.base_ref,
                "target_ref": config.target_ref or push_ref,
            }
        else:
            # Uncommitted scope needs no parameters
            return {}

"""Application use cases for local Git trigger hook lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelens.plugin.application.plugin_manager import PluginManager
from codelens.plugin.domain.models import HookEvent, PluginProfileSource, PluginRecord
from codelens.plugin.domain.ports import HookInstallerPort, TriggerRepositoryValidatorPort


class HookConfigurationError(ValueError):
    """Reject malformed or unsupported local-hook plugin configuration."""


class HookInstallationError(RuntimeError):
    """Report a recoverable failure while synchronizing Git hook files."""


@dataclass(frozen=True)
class RepositoryHookStatus:
    """Installation state for the selected events in one configured repository."""

    repository_path: Path
    hooks: dict[HookEvent, bool]

    @property
    def is_installed(self) -> bool:
        """Return true only when at least one selected hook is installed."""

        return bool(self.hooks) and all(self.hooks.values())

    @property
    def hook_path(self) -> Path | None:
        """Return the single selected hook path for legacy clients when unambiguous."""

        if len(self.hooks) != 1:
            return None
        event = next(iter(self.hooks))
        return self.repository_path / ".git" / "hooks" / event.value


class TriggerHookService:
    """Synchronize local Git hooks with persisted trigger configuration.

    Repository paths pass through the workspace access boundary before any hook
    file is read or written. Synchronization removes only CodeLens-owned hook
    fragments and preserves pre-existing user hook content.
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        hook_installer: HookInstallerPort,
        repository_validator: TriggerRepositoryValidatorPort,
        port: int,
    ) -> None:
        self._plugin_manager = plugin_manager
        self._hook_installer = hook_installer
        self._repository_validator = repository_validator
        self._port = port

    async def enable_trigger(self, plugin_id: str) -> PluginRecord | None:
        """Enable a trigger and synchronize hooks already present in its config."""

        current = await self._plugin_manager.get_plugin(plugin_id)
        if current is None:
            return None
        if not self._is_local_hook(current):
            return await self._plugin_manager.enable_trigger(plugin_id)
        self._plugin_manager.validate_trigger_config(current, current.trigger_config)
        repositories = await self._validated_repositories(current.trigger_config)
        previous = await self._capture_state(repositories)
        try:
            await self._apply_state(
                {repository: self._events(current) for repository in repositories}
            )
            return await self._plugin_manager.enable_trigger(plugin_id)
        except BaseException:
            await self._restore_state(previous)
            raise

    async def disable_trigger(self, plugin_id: str) -> PluginRecord | None:
        """Remove configured local hooks before persisting the disabled state."""

        current = await self._plugin_manager.get_plugin(plugin_id)
        if current is None:
            return None
        if not self._is_local_hook(current):
            return await self._plugin_manager.disable_trigger(plugin_id)
        repositories = await self._validated_repositories(current.trigger_config)
        previous = await self._capture_state(repositories)
        try:
            await self._apply_state({repository: () for repository in repositories})
            return await self._plugin_manager.disable_trigger(plugin_id)
        except BaseException:
            await self._restore_state(previous)
            raise

    async def update_config(
        self,
        plugin_id: str,
        config: dict[str, Any],
        *,
        profile_source: PluginProfileSource | None = None,
        should_replace_profile_source: bool = False,
    ) -> PluginRecord | None:
        """Validate, persist, and synchronize a trigger configuration update."""

        current = await self._plugin_manager.get_plugin(plugin_id)
        if current is None:
            return None
        if not self._is_local_hook(current):
            return await self._plugin_manager.update_trigger_config(
                plugin_id,
                config,
                profile_source=profile_source,
                should_replace_profile_source=should_replace_profile_source,
            )

        merged = {**current.trigger_config, **config}
        self._plugin_manager.validate_trigger_config(current, merged)
        repositories = await self._validated_repositories(merged)
        old_repositories = await self._validated_repositories(current.trigger_config)
        canonical_config = {
            **config,
            "repository_paths": [str(repository) for repository in repositories],
        }
        events = self._events(current, merged)
        affected_repositories = tuple(dict.fromkeys((*old_repositories, *repositories)))
        previous = await self._capture_state(affected_repositories)
        desired = {
            repository: (events if current.trigger_enabled and repository in repositories else ())
            for repository in affected_repositories
        }
        try:
            await self._apply_state(desired)
            return await self._plugin_manager.update_trigger_config(
                plugin_id,
                canonical_config,
                profile_source=profile_source,
                should_replace_profile_source=should_replace_profile_source,
            )
        except BaseException:
            await self._restore_state(previous)
            raise

    async def install_configured(self, plugin_id: str) -> PluginRecord | None:
        """Install selected hooks in every configured repository."""

        record = await self._plugin_manager.get_plugin(plugin_id)
        if record is None:
            return None
        if not record.trigger_enabled:
            raise HookConfigurationError("trigger plugin must be enabled before installing hooks")
        self._require_local_hook(record)
        repositories = await self._validated_repositories(record.trigger_config)
        previous = await self._capture_state(repositories)
        try:
            await self._apply_state(
                {repository: self._events(record) for repository in repositories}
            )
        except BaseException:
            await self._restore_state(previous)
            raise
        return record

    async def uninstall_configured(self, plugin_id: str) -> PluginRecord | None:
        """Remove CodeLens hook fragments from every configured repository."""

        record = await self._plugin_manager.get_plugin(plugin_id)
        if record is None:
            return None
        self._require_local_hook(record)
        repositories = await self._validated_repositories(record.trigger_config)
        previous = await self._capture_state(repositories)
        try:
            await self._apply_state({repository: () for repository in repositories})
        except BaseException:
            await self._restore_state(previous)
            raise
        return record

    async def get_status(self, plugin_id: str) -> tuple[RepositoryHookStatus, ...] | None:
        """Return real hook state for every configured, validated repository."""

        record = await self._plugin_manager.get_plugin(plugin_id)
        if record is None:
            return None
        self._require_local_hook(record)
        repositories = await self._validated_repositories(record.trigger_config)
        selected_events = self._events(record)
        statuses: list[RepositoryHookStatus] = []
        for repository in repositories:
            installed = await self._hook_installer.is_installed(repository)
            statuses.append(
                RepositoryHookStatus(
                    repository_path=repository,
                    hooks={event: installed.get(event, False) for event in selected_events},
                )
            )
        return tuple(statuses)

    async def _validated_repositories(self, config: dict[str, Any]) -> tuple[Path, ...]:
        repositories: list[Path] = []
        for repository in self._repository_paths(config):
            canonical_path = await self._repository_validator.validate_repository(repository)
            repositories.append(canonical_path)
        return tuple(dict.fromkeys(repositories))

    @staticmethod
    def _repository_paths(config: dict[str, Any]) -> tuple[Path, ...]:
        raw_paths = config.get("repository_paths", [])
        if not isinstance(raw_paths, list) or any(
            not isinstance(path, str) or not path.strip() for path in raw_paths
        ):
            raise HookConfigurationError("repository_paths must be an array of non-empty paths")
        return tuple(Path(path).expanduser() for path in raw_paths)

    @staticmethod
    def _is_local_hook(record: PluginRecord) -> bool:
        trigger = record.manifest.trigger
        return trigger is not None and trigger.trigger_type == "local-hook"

    @classmethod
    def _require_local_hook(cls, record: PluginRecord) -> None:
        if not cls._is_local_hook(record):
            raise HookConfigurationError("plugin does not provide local Git hooks")

    @staticmethod
    def _events(
        record: PluginRecord,
        config: dict[str, Any] | None = None,
    ) -> tuple[HookEvent, ...]:
        trigger = record.manifest.trigger
        if trigger is None:
            raise HookConfigurationError("plugin does not provide a trigger capability")
        raw_events = (config or record.trigger_config).get("events", [])
        if not isinstance(raw_events, list) or any(
            not isinstance(event, str) for event in raw_events
        ):
            raise HookConfigurationError("events must be an array of event names")
        supported_events = set(trigger.supported_events)
        if any(event not in supported_events for event in raw_events):
            raise HookConfigurationError("trigger configuration contains an unsupported event")
        try:
            events = tuple(HookEvent(event) for event in dict.fromkeys(raw_events))
        except ValueError as error:
            raise HookConfigurationError(
                "trigger configuration contains an invalid event"
            ) from error
        if HookEvent.WEBHOOK in events:
            raise HookConfigurationError("webhook events cannot be installed as local Git hooks")
        return events

    async def _capture_state(
        self,
        repositories: tuple[Path, ...],
    ) -> dict[Path, tuple[HookEvent, ...]]:
        state: dict[Path, tuple[HookEvent, ...]] = {}
        for repository in repositories:
            try:
                installed = await self._hook_installer.is_installed(repository)
            except (OSError, UnicodeError, ValueError) as error:
                raise HookInstallationError("Git hook state could not be read") from error
            state[repository] = tuple(
                event
                for event in (HookEvent.POST_COMMIT, HookEvent.PRE_PUSH)
                if installed.get(event, False)
            )
        return state

    async def _apply_state(
        self,
        desired: dict[Path, tuple[HookEvent, ...]],
    ) -> None:
        try:
            for repository, events in desired.items():
                await self._hook_installer.uninstall_hooks(repository)
                if events:
                    await self._hook_installer.install_hooks(repository, events, self._port)
        except (OSError, UnicodeError, ValueError) as error:
            raise HookInstallationError("Git hooks could not be synchronized") from error

    async def _restore_state(
        self,
        previous: dict[Path, tuple[HookEvent, ...]],
    ) -> None:
        try:
            for repository, events in previous.items():
                await self._hook_installer.uninstall_hooks(repository)
                if events:
                    await self._hook_installer.install_hooks(repository, events, self._port)
        except (OSError, UnicodeError, ValueError) as error:
            raise HookInstallationError(
                "Git hook synchronization failed and the previous state could not be restored"
            ) from error

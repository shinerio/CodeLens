import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from codelens.plugin.application.hook_management import (
    HookInstallationError,
    TriggerHookService,
)
from codelens.plugin.application.plugin_manager import PluginManager
from codelens.plugin.domain.models import (
    HookEvent,
    PluginManifest,
    PluginRecord,
    TriggerCapability,
)
from codelens.plugin.domain.ports import (
    HookInstallerPort,
    PluginInstallerPort,
    PluginStorePort,
)


class MemoryPluginStore:
    def __init__(self, record: PluginRecord) -> None:
        self.record = record

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return (self.record,)

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        return self.record if plugin_id == self.record.plugin_id else None

    async def save_plugin(self, record: PluginRecord) -> None:
        self.record = record

    async def delete_plugin(self, plugin_id: str) -> bool:
        return False


class CanonicalRepositoryValidator:
    async def validate_repository(self, repository_path: Path) -> Path:
        return await asyncio.to_thread(repository_path.resolve)


class FailingHookInstaller:
    def __init__(self, failing_repository: Path) -> None:
        self._failing_repository = failing_repository.resolve()
        self.installed: dict[Path, tuple[HookEvent, ...]] = {}
        self.has_failed = False

    async def install_hooks(
        self,
        repository_path: Path,
        events: tuple[HookEvent, ...],
        port: int,
    ) -> None:
        del port
        if repository_path == self._failing_repository and not self.has_failed:
            self.has_failed = True
            raise OSError("simulated installation failure")
        self.installed[repository_path] = events

    async def uninstall_hooks(self, repository_path: Path) -> None:
        self.installed.pop(repository_path, None)

    async def is_installed(self, repository_path: Path) -> dict[HookEvent, bool]:
        events = self.installed.get(repository_path, ())
        return {event: event in events for event in HookEvent}


def _record(repositories: tuple[Path, ...]) -> PluginRecord:
    manifest = PluginManifest(
        plugin_id="local",
        name="Local",
        version="1.0.0",
        description="",
        author="test",
        platform="local",
        capabilities={
            "trigger": TriggerCapability(
                trigger_type="local-hook",
                supported_events=("post-commit", "pre-push"),
                entry_point="local_hook_trigger:LocalHookTriggerAdapter",
                config_schema={
                    "type": "object",
                    "properties": {
                        "repository_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "events": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            )
        },
    )
    return PluginRecord(
        plugin_id="local",
        manifest=manifest,
        is_builtin=True,
        install_path=None,
        trigger_enabled=False,
        report_enabled=False,
        report_auto_export=False,
        trigger_config={
            "repository_paths": [str(repository) for repository in repositories],
            "events": ["post-commit"],
        },
    )


async def test_enable_failure_restores_hooks_and_keeps_trigger_disabled(
    tmp_path: Path,
) -> None:
    first_repository = tmp_path / "first"
    second_repository = tmp_path / "second"
    store = MemoryPluginStore(_record((first_repository, second_repository)))
    installer = FailingHookInstaller(second_repository)
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, object()),
        tmp_path / "plugins",
    )
    service = TriggerHookService(
        manager,
        cast(HookInstallerPort, installer),
        CanonicalRepositoryValidator(),
        8765,
    )

    with pytest.raises(HookInstallationError):
        await service.enable_trigger("local")

    assert store.record.trigger_enabled is False
    assert installer.installed == {}


async def test_disabling_trigger_removes_hooks_before_persisting_state(
    tmp_path: Path,
) -> None:
    repository = (tmp_path / "repository").resolve()
    store = MemoryPluginStore(replace(_record((repository,)), trigger_enabled=True))
    installer = FailingHookInstaller(tmp_path / "never-fails")
    installer.installed[repository] = (HookEvent.POST_COMMIT,)
    manager = PluginManager(
        cast(PluginStorePort, store),
        cast(PluginInstallerPort, object()),
        tmp_path / "plugins",
    )
    service = TriggerHookService(
        manager,
        cast(HookInstallerPort, installer),
        CanonicalRepositoryValidator(),
        8765,
    )

    result = await service.disable_trigger("local")

    assert result is not None
    assert result.trigger_enabled is False
    assert installer.installed == {}

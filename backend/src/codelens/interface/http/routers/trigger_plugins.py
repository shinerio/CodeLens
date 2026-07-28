"""HTTP router for trigger plugin management."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from codelens.interface.http.dependencies import HttpComponents, HttpProblem, get_components
from codelens.trigger.domain.models import HookEvent, TriggerConfig

router = APIRouter(prefix="/api/trigger-plugins", tags=["trigger-plugins"])
_LOGGER = logging.getLogger("codelens.trigger_plugins")


class TriggerManifestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    entry_point: str
    trigger_type: str
    supported_events: list[str]
    config_schema: dict
    min_codelens_version: str | None


class TriggerConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_paths: list[str]
    events: list[str]
    scope_type: str
    base_ref: str | None
    target_ref: str | None
    selected_agents: list[str]
    prompt_locale: str
    debounce_seconds: int
    extra: dict


class TriggerRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    manifest: TriggerManifestResponse
    is_enabled: bool
    is_builtin: bool
    install_path: str | None
    config: TriggerConfigResponse


class UpdateTriggerConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_paths: list[str] | None = None
    events: list[str] | None = None
    scope_type: str | None = None
    base_ref: str | None = None
    target_ref: str | None = None
    selected_agents: list[str] | None = None
    prompt_locale: str | None = None
    debounce_seconds: int | None = None


class InstallHooksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_paths: Annotated[list[str], Field(min_length=1)]


class HookStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_path: str
    hooks: dict[str, bool]


def _to_response(record) -> TriggerRecordResponse:
    return TriggerRecordResponse(
        plugin_id=record.plugin_id,
        manifest=TriggerManifestResponse(
            plugin_id=record.manifest.plugin_id,
            name=record.manifest.name,
            version=record.manifest.version,
            description=record.manifest.description,
            author=record.manifest.author,
            entry_point=record.manifest.entry_point,
            trigger_type=record.manifest.trigger_type.value,
            supported_events=[e.value for e in record.manifest.supported_events],
            config_schema=record.manifest.config_schema,
            min_codelens_version=record.manifest.min_codelens_version,
        ),
        is_enabled=record.is_enabled,
        is_builtin=record.is_builtin,
        install_path=record.install_path,
        config=TriggerConfigResponse(
            repository_paths=list(record.config.repository_paths),
            events=[e.value for e in record.config.events],
            scope_type=record.config.scope_type,
            base_ref=record.config.base_ref,
            target_ref=record.config.target_ref,
            selected_agents=list(record.config.selected_agents),
            prompt_locale=record.config.prompt_locale,
            debounce_seconds=record.config.debounce_seconds,
            extra=record.config.extra,
        ),
    )


@router.get("", response_model=list[TriggerRecordResponse])
async def list_trigger_plugins(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[TriggerRecordResponse]:
    """Return all installed trigger plugins."""

    records = await components.trigger_plugin_manager.list_plugins()
    return [_to_response(r) for r in records]


@router.put("/{plugin_id}/enable", response_model=TriggerRecordResponse)
async def enable_trigger_plugin(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> TriggerRecordResponse:
    """Enable one installed trigger plugin."""

    record = await components.trigger_plugin_manager.enable_plugin(plugin_id)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Trigger plugin '{plugin_id}' is not installed."
        ) from None
    return _to_response(record)


@router.put("/{plugin_id}/disable", response_model=TriggerRecordResponse)
async def disable_trigger_plugin(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> TriggerRecordResponse:
    """Disable one installed trigger plugin."""

    record = await components.trigger_plugin_manager.disable_plugin(plugin_id)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Trigger plugin '{plugin_id}' is not installed."
        ) from None
    return _to_response(record)


@router.put("/{plugin_id}/config", response_model=TriggerRecordResponse)
async def update_trigger_config(
    plugin_id: str,
    request: UpdateTriggerConfigRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> TriggerRecordResponse:
    """Update configuration for one trigger plugin."""

    # Get current record
    current = await components.trigger_plugin_manager.get_plugin(plugin_id)
    if current is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Trigger plugin '{plugin_id}' is not installed."
        ) from None

    # Track old repository paths for auto-install/uninstall
    old_paths = set(current.config.repository_paths)

    # Validate event values before merging
    valid_event_values = {e.value for e in HookEvent}
    if request.events is not None:
        invalid = [e for e in request.events if e not in valid_event_values]
        if invalid:
            raise HttpProblem(
                400,
                "invalid_event_type",
                f"Unknown event type(s): {invalid}. Valid values: {[e.value for e in HookEvent]}",
            )
        validated_events = tuple(HookEvent(e) for e in request.events)
    else:
        validated_events = None

    # Merge with existing config
    new_config = TriggerConfig(
        repository_paths=tuple(request.repository_paths) if request.repository_paths is not None else current.config.repository_paths,
        events=validated_events if validated_events is not None else current.config.events,
        scope_type=request.scope_type if request.scope_type is not None else current.config.scope_type,
        base_ref=request.base_ref if request.base_ref is not None else current.config.base_ref,
        target_ref=request.target_ref if request.target_ref is not None else current.config.target_ref,
        selected_agents=tuple(request.selected_agents) if request.selected_agents is not None else current.config.selected_agents,
        prompt_locale=request.prompt_locale if request.prompt_locale is not None else current.config.prompt_locale,
        debounce_seconds=request.debounce_seconds if request.debounce_seconds is not None else current.config.debounce_seconds,
        extra=current.config.extra,
    )

    record = await components.trigger_plugin_manager.update_config(plugin_id, new_config)
    if record is None:
        raise HttpProblem(
            500, "config_update_failed", "Failed to update trigger plugin configuration."
        ) from None

    # Auto-install/uninstall hooks based on repository path changes
    new_paths = set(record.config.repository_paths)
    added_paths = new_paths - old_paths
    removed_paths = old_paths - new_paths

    # Auto-install for added paths (only if plugin is enabled)
    if record.is_enabled:
        for path_str in added_paths:
            repo_path = Path(path_str)
            try:
                await components.hook_installer.install_hooks(
                    repository_path=repo_path,
                    events=record.config.events,
                    port=components.settings.port,
                )
                _LOGGER.info("Auto-installed hooks for %s in %s", plugin_id, repo_path)
            except Exception as error:
                _LOGGER.warning("Failed to auto-install hooks in %s: %s", repo_path, error)

    # Auto-uninstall for removed paths (always, regardless of enabled state)
    for path_str in removed_paths:
        repo_path = Path(path_str)
        try:
            await components.hook_installer.uninstall_hooks(repository_path=repo_path)
            _LOGGER.info("Auto-uninstalled hooks from %s", repo_path)
        except Exception as error:
            _LOGGER.warning("Failed to auto-uninstall hooks from %s: %s", repo_path, error)

    return _to_response(record)


@router.post("/{plugin_id}/install-hooks", status_code=204)
async def install_hooks(
    plugin_id: str,
    request: InstallHooksRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> None:
    """Install git hooks for specified repositories."""

    from pathlib import Path

    record = await components.trigger_plugin_manager.get_plugin(plugin_id)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Trigger plugin '{plugin_id}' is not installed."
        ) from None

    if not record.is_enabled:
        raise HttpProblem(
            400, "plugin_disabled", f"Trigger plugin '{plugin_id}' must be enabled before installing hooks."
        ) from None

    # Install hooks for each repository
    for repo_path_str in request.repository_paths:
        repo_path = Path(repo_path_str)
        try:
            await components.hook_installer.install_hooks(
                repository_path=repo_path,
                events=record.config.events,
                port=components.settings.port,
            )
            _LOGGER.info("Installed hooks for %s in %s", plugin_id, repo_path)
        except Exception as error:
            raise HttpProblem(
                500, "hook_install_failed", f"Failed to install hooks in {repo_path}: {error}"
            ) from None


@router.post("/{plugin_id}/uninstall-hooks", status_code=204)
async def uninstall_hooks(
    plugin_id: str,
    request: InstallHooksRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> None:
    """Uninstall git hooks from specified repositories."""

    from pathlib import Path

    record = await components.trigger_plugin_manager.get_plugin(plugin_id)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Trigger plugin '{plugin_id}' is not installed."
        ) from None

    # Uninstall hooks from each repository
    for repo_path_str in request.repository_paths:
        repo_path = Path(repo_path_str)
        try:
            await components.hook_installer.uninstall_hooks(repository_path=repo_path)
            _LOGGER.info("Uninstalled hooks for %s from %s", plugin_id, repo_path)
        except Exception as error:
            raise HttpProblem(
                500, "hook_uninstall_failed", f"Failed to uninstall hooks from {repo_path}: {error}"
            ) from None


@router.get("/{plugin_id}/hook-status", response_model=list[HookStatusResponse])
async def get_hook_status(
    plugin_id: str,
    repository_paths: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[HookStatusResponse]:
    """Get hook installation status for specified repositories."""

    from pathlib import Path

    record = await components.trigger_plugin_manager.get_plugin(plugin_id)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Trigger plugin '{plugin_id}' is not installed."
        ) from None

    # Parse comma-separated repository paths
    paths = [p.strip() for p in repository_paths.split(",") if p.strip()]

    results = []
    for repo_path_str in paths:
        repo_path = Path(repo_path_str)
        try:
            status = await components.hook_installer.is_installed(repository_path=repo_path)
            results.append(HookStatusResponse(
                repository_path=repo_path_str,
                hooks={event.value: installed for event, installed in status.items()},
            ))
        except Exception as error:
            _LOGGER.warning("Failed to check hook status for %s: %s", repo_path, error)
            results.append(HookStatusResponse(
                repository_path=repo_path_str,
                hooks={},
            ))

    return results

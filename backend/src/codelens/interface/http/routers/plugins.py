"""Unified plugin management API routes."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from codelens.interface.http.dependencies import (
    HttpComponents,
    HttpProblem,
    get_components,
)
from codelens.plugin.application.hook_management import (
    HookConfigurationError,
    HookInstallationError,
    RepositoryHookStatus,
)
from codelens.plugin.domain.models import (
    PluginCapabilityError,
    PluginConfigurationError,
    PluginInstallError,
    PluginRecord,
    ReportCapability,
    TriggerCapability,
)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])
_LOGGER = logging.getLogger("codelens.plugins")


class PluginManifestResponse(BaseModel):
    """Plugin manifest metadata."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    platform: str
    capabilities: dict[str, Any]
    min_codelens_version: str | None
    name_i18n: dict[str, str] = Field(default_factory=dict)
    description_i18n: dict[str, str] = Field(default_factory=dict)


class PluginRecordResponse(BaseModel):
    """Plugin installation and configuration state."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    manifest: PluginManifestResponse
    is_builtin: bool
    install_path: str | None
    trigger_enabled: bool
    report_enabled: bool
    report_auto_export: bool
    trigger_config: dict[str, Any]
    report_config: dict[str, Any]

    @classmethod
    def from_domain(cls, record: PluginRecord) -> "PluginRecordResponse":
        """Convert domain record to API response."""
        capabilities: dict[str, Any] = {}
        for key, cap in record.manifest.capabilities.items():
            if isinstance(cap, TriggerCapability):
                capabilities[key] = {
                    "trigger_type": cap.trigger_type,
                    "supported_events": list(cap.supported_events),
                    "entry_point": cap.entry_point,
                    "config_schema": cap.config_schema,
                }
            elif isinstance(cap, ReportCapability):
                capabilities[key] = {
                    "entry_point": cap.entry_point,
                    "config_schema": cap.config_schema,
                }
        return cls(
            plugin_id=record.plugin_id,
            manifest=PluginManifestResponse(
                plugin_id=record.manifest.plugin_id,
                name=record.manifest.name,
                version=record.manifest.version,
                description=record.manifest.description,
                author=record.manifest.author,
                platform=record.manifest.platform,
                capabilities=capabilities,
                min_codelens_version=record.manifest.min_codelens_version,
                name_i18n=record.manifest.name_i18n,
                description_i18n=record.manifest.description_i18n,
            ),
            is_builtin=record.is_builtin,
            install_path=record.install_path,
            trigger_enabled=record.trigger_enabled,
            report_enabled=record.report_enabled,
            report_auto_export=record.report_auto_export,
            trigger_config=record.trigger_config,
            report_config=record.report_config,
        )


class InstallPluginRequest(BaseModel):
    """Install a plugin from a Git repository."""

    model_config = ConfigDict(extra="forbid")

    git_url: Annotated[str, Field(min_length=1, max_length=512)]
    ref: Annotated[str | None, Field(min_length=1, max_length=128)] = None


class InstallPluginResponse(BaseModel):
    """Result of plugin installation."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    install_path: str
    installed_at: str


class UpdateConfigRequest(BaseModel):
    """Update plugin capability configuration."""

    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any]


class HookStatusResponse(BaseModel):
    """Git hook installation status."""

    model_config = ConfigDict(extra="forbid")

    is_installed: bool
    hook_path: str | None
    repository_path: str
    repositories: list["RepositoryHookStatusResponse"]


class RepositoryHookStatusResponse(BaseModel):
    """Selected Git hook state for one configured repository."""

    model_config = ConfigDict(extra="forbid")

    repository_path: str
    hooks: dict[str, bool]
    is_installed: bool


def _hook_status_response(
    statuses: Sequence[RepositoryHookStatus],
) -> HookStatusResponse:
    repositories = [
        RepositoryHookStatusResponse(
            repository_path=str(status.repository_path),
            hooks={event.value: installed for event, installed in status.hooks.items()},
            is_installed=status.is_installed,
        )
        for status in statuses
    ]
    single_status = statuses[0] if len(statuses) == 1 else None
    return HookStatusResponse(
        is_installed=bool(statuses) and all(status.is_installed for status in statuses),
        hook_path=(
            str(single_status.hook_path)
            if single_status is not None and single_status.hook_path is not None
            else None
        ),
        repository_path=(str(single_status.repository_path) if single_status is not None else ""),
        repositories=repositories,
    )


def _raise_hook_problem(error: HookConfigurationError) -> None:
    raise HttpProblem(400, "invalid_hook_configuration", str(error)) from error


def _raise_hook_installation_problem(error: HookInstallationError) -> None:
    raise HttpProblem(500, "hook_install_failed", str(error)) from error


def _raise_plugin_problem(error: PluginCapabilityError | PluginConfigurationError) -> None:
    code = (
        "invalid_plugin_capability"
        if isinstance(error, PluginCapabilityError)
        else "invalid_plugin_configuration"
    )
    raise HttpProblem(400, code, str(error)) from error


@router.get("", response_model=list[PluginRecordResponse])
async def list_plugins(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[PluginRecordResponse]:
    """Return all installed plugins."""

    records = await components.plugin_manager.list_plugins()
    return [PluginRecordResponse.from_domain(record) for record in records]


@router.post("/install", response_model=InstallPluginResponse, status_code=201)
async def install_plugin(
    request: InstallPluginRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> InstallPluginResponse:
    """Install a plugin from a Git repository."""

    _LOGGER.info("Installing external plugin")
    try:
        record = await components.plugin_manager.install_from_git(
            git_url=request.git_url,
            ref=request.ref,
        )
    except PluginInstallError as error:
        raise HttpProblem(400, "plugin_install_failed", str(error)) from error
    _LOGGER.info("Plugin installed: %s", record.plugin_id)
    return InstallPluginResponse(
        plugin_id=record.plugin_id,
        install_path=record.install_path or "",
        installed_at=datetime.now(UTC).isoformat(),
    )


@router.delete("/{plugin_id}", status_code=204)
async def uninstall_plugin(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> None:
    """Uninstall a plugin."""

    _LOGGER.info("Uninstalling plugin: %s", plugin_id)
    try:
        success = await components.plugin_manager.uninstall_plugin(plugin_id)
    except PluginInstallError as error:
        raise HttpProblem(400, "plugin_uninstall_rejected", str(error)) from error
    if not success:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    _LOGGER.info("Plugin uninstalled: %s", plugin_id)


@router.put("/{plugin_id}/trigger/enable", response_model=PluginRecordResponse)
async def enable_trigger(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Enable trigger capability for a plugin."""

    _LOGGER.info("Enabling trigger for plugin: %s", plugin_id)
    try:
        record = await components.trigger_hooks.enable_trigger(plugin_id)
    except HookConfigurationError as error:
        _raise_hook_problem(error)
    except HookInstallationError as error:
        _raise_hook_installation_problem(error)
    except (PluginCapabilityError, PluginConfigurationError) as error:
        _raise_plugin_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return PluginRecordResponse.from_domain(record)


@router.put("/{plugin_id}/trigger/disable", response_model=PluginRecordResponse)
async def disable_trigger(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Disable trigger capability for a plugin."""

    _LOGGER.info("Disabling trigger for plugin: %s", plugin_id)
    try:
        record = await components.trigger_hooks.disable_trigger(plugin_id)
    except HookConfigurationError as error:
        _raise_hook_problem(error)
    except HookInstallationError as error:
        _raise_hook_installation_problem(error)
    except PluginCapabilityError as error:
        _raise_plugin_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return PluginRecordResponse.from_domain(record)


@router.put("/{plugin_id}/report/enable", response_model=PluginRecordResponse)
async def enable_report(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Enable report capability for a plugin."""

    _LOGGER.info("Enabling report for plugin: %s", plugin_id)
    try:
        record = await components.plugin_manager.enable_report(plugin_id)
    except (PluginCapabilityError, PluginConfigurationError) as error:
        _raise_plugin_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return PluginRecordResponse.from_domain(record)


@router.put("/{plugin_id}/report/disable", response_model=PluginRecordResponse)
async def disable_report(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Disable report capability for a plugin."""

    _LOGGER.info("Disabling report for plugin: %s", plugin_id)
    try:
        record = await components.plugin_manager.disable_report(plugin_id)
    except PluginCapabilityError as error:
        _raise_plugin_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return PluginRecordResponse.from_domain(record)


@router.put("/{plugin_id}/trigger/config", response_model=PluginRecordResponse)
async def update_trigger_config(
    plugin_id: str,
    request: UpdateConfigRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Update trigger configuration for a plugin."""

    _LOGGER.info("Updating trigger config for plugin: %s", plugin_id)
    try:
        record = await components.trigger_hooks.update_config(plugin_id, request.config)
    except HookConfigurationError as error:
        _raise_hook_problem(error)
    except HookInstallationError as error:
        _raise_hook_installation_problem(error)
    except (PluginCapabilityError, PluginConfigurationError) as error:
        _raise_plugin_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return PluginRecordResponse.from_domain(record)


@router.put("/{plugin_id}/report/config", response_model=PluginRecordResponse)
async def update_report_config(
    plugin_id: str,
    request: UpdateConfigRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Update report configuration for a plugin."""

    _LOGGER.info("Updating report config for plugin: %s", plugin_id)
    try:
        record = await components.plugin_manager.update_report_config(
            plugin_id, request.config
        )
    except (PluginCapabilityError, PluginConfigurationError) as error:
        _raise_plugin_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return PluginRecordResponse.from_domain(record)


@router.put("/{plugin_id}/report/auto-export", response_model=PluginRecordResponse)
async def set_auto_export(
    plugin_id: str,
    enabled: bool,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Enable or disable automatic export for a plugin."""

    _LOGGER.info("Setting auto-export for plugin %s: %s", plugin_id, enabled)
    try:
        record = await components.plugin_manager.set_auto_export(plugin_id, enabled)
    except PluginCapabilityError as error:
        _raise_plugin_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return PluginRecordResponse.from_domain(record)


@router.post("/{plugin_id}/trigger/install-hooks", response_model=HookStatusResponse)
async def install_hooks(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> HookStatusResponse:
    """Install Git hooks for a trigger plugin."""

    _LOGGER.info("Installing hooks for plugin: %s", plugin_id)
    try:
        record = await components.trigger_hooks.install_configured(plugin_id)
    except HookConfigurationError as error:
        _raise_hook_problem(error)
    except HookInstallationError as error:
        _raise_hook_installation_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    statuses = await components.trigger_hooks.get_status(plugin_id)
    return _hook_status_response(statuses or ())


@router.post("/{plugin_id}/trigger/uninstall-hooks", response_model=HookStatusResponse)
async def uninstall_hooks(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> HookStatusResponse:
    """Uninstall Git hooks for a trigger plugin."""

    _LOGGER.info("Uninstalling hooks for plugin: %s", plugin_id)
    try:
        record = await components.trigger_hooks.uninstall_configured(plugin_id)
    except HookConfigurationError as error:
        _raise_hook_problem(error)
    except HookInstallationError as error:
        _raise_hook_installation_problem(error)
    if record is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    statuses = await components.trigger_hooks.get_status(plugin_id)
    return _hook_status_response(statuses or ())


@router.get("/{plugin_id}/trigger/hook-status", response_model=HookStatusResponse)
async def get_hook_status(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> HookStatusResponse:
    """Get Git hook installation status for a trigger plugin."""

    _LOGGER.info("Getting hook status for plugin: %s", plugin_id)
    try:
        statuses = await components.trigger_hooks.get_status(plugin_id)
    except HookConfigurationError as error:
        _raise_hook_problem(error)
    if statuses is None:
        raise HttpProblem(404, "plugin_not_found", f"Plugin {plugin_id} not found.")
    return _hook_status_response(statuses)

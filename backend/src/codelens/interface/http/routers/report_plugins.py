"""HTTP router for report plugin management."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from codelens.interface.http.dependencies import HttpComponents, HttpProblem, get_components
from codelens.reporting.infrastructure.git_installer import PluginInstallError

router = APIRouter(prefix="/api/report-plugins", tags=["report-plugins"])
_LOGGER = logging.getLogger("codelens.report_plugins")


class PluginManifestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    entry_point: str
    config_schema: dict
    min_codelens_version: str | None


class PluginRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    manifest: PluginManifestResponse
    is_enabled: bool
    is_builtin: bool
    install_path: str | None
    config: dict
    auto_export: bool


class InstallPluginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git_url: Annotated[str, Field(min_length=1, max_length=2048)]
    ref: Annotated[str | None, Field(max_length=512)] = None


class UpdateConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict


class SetAutoExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ExportResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    task_id: str
    success: bool
    output_path: str | None
    error: str | None
    exported_at: str


def _to_response(record) -> PluginRecordResponse:
    return PluginRecordResponse(
        plugin_id=record.plugin_id,
        manifest=PluginManifestResponse(
            plugin_id=record.manifest.plugin_id,
            name=record.manifest.name,
            version=record.manifest.version,
            description=record.manifest.description,
            author=record.manifest.author,
            entry_point=record.manifest.entry_point,
            config_schema=record.manifest.config_schema,
            min_codelens_version=record.manifest.min_codelens_version,
        ),
        is_enabled=record.is_enabled,
        is_builtin=record.is_builtin,
        install_path=record.install_path,
        config=record.config,
        auto_export=record.auto_export,
    )


@router.get("", response_model=list[PluginRecordResponse])
async def list_plugins(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[PluginRecordResponse]:
    """Return all installed report plugins."""

    records = await components.plugin_manager.list_plugins()
    return [_to_response(r) for r in records]


@router.post("/install", response_model=PluginRecordResponse, status_code=201)
async def install_plugin(
    request: InstallPluginRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Install a report plugin from a Git repository."""

    try:
        manifest = await components.plugin_manager.install_from_git(request.git_url, request.ref)
    except PluginInstallError as error:
        raise HttpProblem(422, "plugin_install_failed", str(error)) from None
    record = await components.plugin_manager.get_plugin(manifest.plugin_id)
    if record is None:
        raise HttpProblem(
            500, "plugin_install_failed", "Plugin was not persisted after install"
        ) from None
    return _to_response(record)


@router.put("/{plugin_id}/enable", response_model=PluginRecordResponse)
async def enable_plugin(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Enable one installed plugin."""

    record = await components.plugin_manager.enable_plugin(plugin_id)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Plugin '{plugin_id}' is not installed."
        ) from None
    return _to_response(record)


@router.put("/{plugin_id}/disable", response_model=PluginRecordResponse)
async def disable_plugin(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Disable one installed plugin."""

    record = await components.plugin_manager.disable_plugin(plugin_id)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Plugin '{plugin_id}' is not installed."
        ) from None
    return _to_response(record)


@router.put("/{plugin_id}/config", response_model=PluginRecordResponse)
async def update_config(
    plugin_id: str,
    request: UpdateConfigRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Update configuration for one plugin."""

    record = await components.plugin_manager.update_config(plugin_id, request.config)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Plugin '{plugin_id}' is not installed."
        ) from None
    return _to_response(record)


@router.put("/{plugin_id}/auto-export", response_model=PluginRecordResponse)
async def set_auto_export(
    plugin_id: str,
    request: SetAutoExportRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> PluginRecordResponse:
    """Enable or disable automatic export for one plugin."""

    record = await components.plugin_manager.set_auto_export(plugin_id, request.enabled)
    if record is None:
        raise HttpProblem(
            404, "plugin_not_found", f"Plugin '{plugin_id}' is not installed."
        ) from None
    return _to_response(record)


@router.delete("/{plugin_id}", status_code=204)
async def uninstall_plugin(
    plugin_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> None:
    """Uninstall an external plugin (built-in plugins cannot be removed)."""

    try:
        removed = await components.plugin_manager.uninstall_plugin(plugin_id)
    except PluginInstallError as error:
        raise HttpProblem(422, "plugin_uninstall_failed", str(error)) from None
    if not removed:
        raise HttpProblem(
            404, "plugin_not_found", f"Plugin '{plugin_id}' is not installed."
        ) from None

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import StringConstraints

from codelens.interface.http.dependencies import HttpComponents, get_components
from codelens.interface.http.dto import (
    ActivateModelGatewayRequest,
    CreateModelGatewayRequest,
    ModelGatewayCatalogResponse,
    ModelGatewayResponse,
    OpenAISettingsResponse,
    UpdateModelGatewayRequest,
    UpdateOpenAISettingsRequest,
)
from codelens.reviewer_catalog.application.provider_settings import ModelGatewayCatalogView

router = APIRouter(prefix="/api/settings", tags=["settings"])

GatewayId = Annotated[
    str,
    StringConstraints(pattern=r"^gateway_[A-Za-z0-9_-]{3,64}$", max_length=72),
]


def _catalog_response(view: ModelGatewayCatalogView) -> ModelGatewayCatalogResponse:
    return ModelGatewayCatalogResponse(
        active_gateway_id=view.active_gateway_id,
        gateways=[
            ModelGatewayResponse(
                gateway_id=gateway.gateway_id,
                name=gateway.name,
                model=gateway.model,
                base_url=gateway.base_url,
                is_active=gateway.is_active,
            )
            for gateway in view.gateways
        ],
    )


@router.get("/model-gateways", response_model=ModelGatewayCatalogResponse)
async def list_model_gateways(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Return every gateway without serializing any stored API key."""

    return _catalog_response(await components.model_gateways.list())


@router.post(
    "/model-gateways",
    response_model=ModelGatewayCatalogResponse,
    status_code=201,
)
async def create_model_gateway(
    request: CreateModelGatewayRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Persist a new named gateway; the first gateway becomes active."""

    return _catalog_response(
        await components.model_gateways.create(
            name=request.name,
            api_key=request.api_key.get_secret_value(),
            model=request.model,
            base_url=str(request.base_url).rstrip("/"),
        )
    )


@router.put("/model-gateways/{gateway_id}", response_model=ModelGatewayCatalogResponse)
async def update_model_gateway(
    gateway_id: GatewayId,
    request: UpdateModelGatewayRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Update one gateway and retain its key when no replacement is supplied."""

    return _catalog_response(
        await components.model_gateways.update(
            gateway_id,
            name=request.name,
            api_key=(
                request.api_key.get_secret_value() if request.api_key is not None else None
            ),
            model=request.model,
            base_url=str(request.base_url).rstrip("/"),
        )
    )


@router.delete("/model-gateways/{gateway_id}", response_model=ModelGatewayCatalogResponse)
async def delete_model_gateway(
    gateway_id: GatewayId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Delete one gateway and select a deterministic fallback when required."""

    return _catalog_response(await components.model_gateways.delete(gateway_id))


@router.put("/active-model-gateway", response_model=ModelGatewayCatalogResponse)
async def activate_model_gateway(
    request: ActivateModelGatewayRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Switch the active runtime gateway without restarting API or Worker."""

    return _catalog_response(await components.model_gateways.activate(request.gateway_id))


@router.get("/openai", response_model=OpenAISettingsResponse)
async def get_openai_settings(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> OpenAISettingsResponse:
    """Return provider readiness without exposing the stored API key."""

    view = await components.get_provider_settings.handle()
    return OpenAISettingsResponse(
        is_configured=view.is_configured,
        model=view.model,
        base_url=view.base_url,
    )


@router.put("/openai", response_model=OpenAISettingsResponse)
async def update_openai_settings(
    request: UpdateOpenAISettingsRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> OpenAISettingsResponse:
    """Validate and atomically replace the model provider Secret Store entry."""

    view = await components.update_provider_settings.handle(
        api_key=request.api_key.get_secret_value(),
        model=request.model,
        base_url=str(request.base_url).rstrip("/"),
    )
    return OpenAISettingsResponse(
        is_configured=view.is_configured,
        model=view.model,
        base_url=view.base_url,
    )

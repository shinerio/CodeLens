import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from codelens.reviewer_catalog.domain.provider_config import (
    ModelGateway,
    ModelGatewayCatalog,
    ModelGatewayStorePort,
    ModelProviderConfig,
    ModelProviderConfigPort,
)
from codelens.shared.domain.errors import DomainError


@dataclass(frozen=True)
class ProviderSettingsView:
    """Expose provider readiness without returning its credential."""

    is_configured: bool
    model: str | None
    base_url: str | None


class GetProviderSettingsHandler:
    """Read a redacted view of the configured model provider."""

    def __init__(self, store: ModelProviderConfigPort) -> None:
        self._store = store

    async def handle(self) -> ProviderSettingsView:
        """Return a read model that never contains the provider credential."""

        config = await self._store.load()
        if config is None:
            return ProviderSettingsView(False, None, None)
        return ProviderSettingsView(True, config.model, config.base_url)


class UpdateProviderSettingsHandler:
    """Replace provider settings through the injected Secret Store port."""

    def __init__(self, store: ModelProviderConfigPort) -> None:
        self._store = store

    async def handle(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
    ) -> ProviderSettingsView:
        """Persist one complete provider configuration and return its redacted view."""

        config = ModelProviderConfig(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
        await self._store.save(config)
        return ProviderSettingsView(True, config.model, config.base_url)


class ModelGatewayNotFoundError(DomainError):
    """Raised when a gateway command references an unknown persistent identifier."""

    code = "model_gateway_not_found"


@dataclass(frozen=True)
class ModelGatewayView:
    """Expose gateway metadata while keeping its API key write-only."""

    gateway_id: str
    name: str
    model: str
    base_url: str
    is_active: bool


@dataclass(frozen=True)
class ModelGatewayCatalogView:
    """Expose the ordered redacted gateway collection to interface adapters."""

    active_gateway_id: str | None
    gateways: tuple[ModelGatewayView, ...]


class ModelGatewaySettingsService:
    """Serialize local gateway commands and preserve one active runtime selection."""

    def __init__(
        self,
        store: ModelGatewayStorePort,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._id_factory = id_factory or (lambda: f"gateway_{uuid.uuid4().hex}")
        self._command_lock = asyncio.Lock()

    async def list(self) -> ModelGatewayCatalogView:
        """Return all persisted gateways without exposing credentials."""

        return self._view(await self._store.load_catalog())

    async def create(
        self,
        *,
        name: str,
        api_key: str,
        model: str,
        base_url: str,
    ) -> ModelGatewayCatalogView:
        """Append a gateway; the first created gateway becomes active automatically."""

        async with self._command_lock:
            catalog = await self._store.load_catalog()
            gateway = ModelGateway(
                gateway_id=self._id_factory(),
                name=name,
                api_key=api_key,
                model=model,
                base_url=base_url,
            )
            updated = ModelGatewayCatalog(
                active_gateway_id=catalog.active_gateway_id or gateway.gateway_id,
                gateways=(*catalog.gateways, gateway),
            )
            await self._store.save_catalog(updated)
            return self._view(updated)

    async def update(
        self,
        gateway_id: str,
        *,
        name: str,
        api_key: str | None,
        model: str,
        base_url: str,
    ) -> ModelGatewayCatalogView:
        """Replace gateway metadata while retaining an omitted write-only API key."""

        async with self._command_lock:
            catalog = await self._store.load_catalog()
            existing = self._find(catalog, gateway_id)
            replacement = ModelGateway(
                gateway_id=existing.gateway_id,
                name=name,
                api_key=api_key if api_key is not None else existing.api_key,
                model=model,
                base_url=base_url,
            )
            updated = ModelGatewayCatalog(
                active_gateway_id=catalog.active_gateway_id,
                gateways=tuple(
                    replacement if gateway.gateway_id == gateway_id else gateway
                    for gateway in catalog.gateways
                ),
            )
            await self._store.save_catalog(updated)
            return self._view(updated)

    async def activate(self, gateway_id: str) -> ModelGatewayCatalogView:
        """Select the gateway that new Agent invocations will read."""

        async with self._command_lock:
            catalog = await self._store.load_catalog()
            self._find(catalog, gateway_id)
            updated = ModelGatewayCatalog(gateway_id, catalog.gateways)
            await self._store.save_catalog(updated)
            return self._view(updated)

    async def delete(self, gateway_id: str) -> ModelGatewayCatalogView:
        """Delete one gateway and deterministically activate the first remaining entry."""

        async with self._command_lock:
            catalog = await self._store.load_catalog()
            self._find(catalog, gateway_id)
            remaining = tuple(
                gateway for gateway in catalog.gateways if gateway.gateway_id != gateway_id
            )
            active_gateway_id = catalog.active_gateway_id
            if active_gateway_id == gateway_id:
                active_gateway_id = remaining[0].gateway_id if remaining else None
            updated = ModelGatewayCatalog(active_gateway_id, remaining)
            await self._store.save_catalog(updated)
            return self._view(updated)

    @staticmethod
    def _find(catalog: ModelGatewayCatalog, gateway_id: str) -> ModelGateway:
        gateway = next(
            (item for item in catalog.gateways if item.gateway_id == gateway_id),
            None,
        )
        if gateway is None:
            raise ModelGatewayNotFoundError("model gateway does not exist")
        return gateway

    @staticmethod
    def _view(catalog: ModelGatewayCatalog) -> ModelGatewayCatalogView:
        return ModelGatewayCatalogView(
            active_gateway_id=catalog.active_gateway_id,
            gateways=tuple(
                ModelGatewayView(
                    gateway_id=gateway.gateway_id,
                    name=gateway.name,
                    model=gateway.model,
                    base_url=gateway.base_url,
                    is_active=gateway.gateway_id == catalog.active_gateway_id,
                )
                for gateway in catalog.gateways
            ),
        )

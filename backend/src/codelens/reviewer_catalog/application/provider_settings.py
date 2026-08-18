import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from codelens.reviewer_catalog.domain.provider_config import (
    GatewayApiType,
    GatewayAvailabilityResult,
    GatewayConnectivityResult,
    ModelGateway,
    ModelGatewayCatalog,
    ModelGatewayProbePort,
    ModelGatewayStorePort,
    ModelProviderVendor,
    ThinkingLevel,
)
from codelens.shared.domain.errors import DomainError


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
    vendor: ModelProviderVendor
    is_active: bool
    api_type: GatewayApiType
    max_tokens: int
    thinking_level: ThinkingLevel
    agent_timeout: int
    max_agent_turns: int
    max_tool_calls: int
    max_identical_tool_results: int
    tool_timeout_seconds: int
    max_retries: int
    retry_backoff_base: float
    retry_max_delay: float
    no_progress_rounds_threshold: int


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
        probe: ModelGatewayProbePort,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._probe = probe
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
        vendor: ModelProviderVendor = "openai",
        api_type: GatewayApiType = "chat_completions",
        max_tokens: int = 65536,
        thinking_level: ThinkingLevel = "disabled",
        agent_timeout: int = 3600,
        max_agent_turns: int = 500,
        max_tool_calls: int = 500,
        max_identical_tool_results: int = 3,
        tool_timeout_seconds: int = 30,
        max_retries: int = 10,
        retry_backoff_base: float = 1.0,
        retry_max_delay: float = 30.0,
        no_progress_rounds_threshold: int = 10,
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
                vendor=vendor,
                api_type=api_type,
                max_tokens=max_tokens,
                thinking_level=thinking_level,
                agent_timeout=agent_timeout,
                max_agent_turns=max_agent_turns,
                max_tool_calls=max_tool_calls,
                max_identical_tool_results=max_identical_tool_results,
                tool_timeout_seconds=tool_timeout_seconds,
                max_retries=max_retries,
                retry_backoff_base=retry_backoff_base,
                retry_max_delay=retry_max_delay,
                no_progress_rounds_threshold=no_progress_rounds_threshold,
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
        vendor: ModelProviderVendor = "openai",
        api_type: GatewayApiType = "chat_completions",
        max_tokens: int = 65536,
        thinking_level: ThinkingLevel = "disabled",
        agent_timeout: int = 3600,
        max_agent_turns: int = 500,
        max_tool_calls: int = 500,
        max_identical_tool_results: int = 3,
        tool_timeout_seconds: int = 30,
        max_retries: int = 10,
        retry_backoff_base: float = 1.0,
        retry_max_delay: float = 30.0,
        no_progress_rounds_threshold: int = 10,
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
                vendor=vendor,
                api_type=api_type,
                max_tokens=max_tokens,
                thinking_level=thinking_level,
                agent_timeout=agent_timeout,
                max_agent_turns=max_agent_turns,
                max_tool_calls=max_tool_calls,
                max_identical_tool_results=max_identical_tool_results,
                tool_timeout_seconds=tool_timeout_seconds,
                max_retries=max_retries,
                retry_backoff_base=retry_backoff_base,
                retry_max_delay=retry_max_delay,
                no_progress_rounds_threshold=no_progress_rounds_threshold,
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

    async def test_connectivity(self, gateway_id: str) -> GatewayConnectivityResult:
        """Probe TCP reachability of one gateway without exposing its credential."""

        catalog = await self._store.load_catalog()
        gateway = self._find(catalog, gateway_id)
        return await self._probe.test_connectivity(gateway.base_url)

    async def test_availability(self, gateway_id: str) -> GatewayAvailabilityResult:
        """Send a minimal ping to verify the LLM behind one gateway can respond."""

        catalog = await self._store.load_catalog()
        gateway = self._find(catalog, gateway_id)
        return await self._probe.test_availability(gateway.provider_config)

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
                    vendor=gateway.vendor,
                    is_active=gateway.gateway_id == catalog.active_gateway_id,
                    api_type=gateway.api_type,
                    max_tokens=gateway.max_tokens,
                    thinking_level=gateway.thinking_level,
                    agent_timeout=gateway.agent_timeout,
                    max_agent_turns=gateway.max_agent_turns,
                    max_tool_calls=gateway.max_tool_calls,
                    max_identical_tool_results=gateway.max_identical_tool_results,
                    tool_timeout_seconds=gateway.tool_timeout_seconds,
                    max_retries=gateway.max_retries,
                    retry_backoff_base=gateway.retry_backoff_base,
                    retry_max_delay=gateway.retry_max_delay,
                    no_progress_rounds_threshold=gateway.no_progress_rounds_threshold,
                )
                for gateway in catalog.gateways
            ),
        )

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelProviderConfig:
    """Hold one provider credential only inside the Secret Store boundary."""

    api_key: str
    model: str
    base_url: str


@dataclass(frozen=True)
class ModelGateway:
    """Describe one named OpenAI-compatible gateway including its write-only credential."""

    gateway_id: str
    name: str
    api_key: str
    model: str
    base_url: str

    @property
    def provider_config(self) -> ModelProviderConfig:
        """Return the active runtime view without gateway-management metadata."""

        return ModelProviderConfig(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
        )


@dataclass(frozen=True)
class ModelGatewayCatalog:
    """Keep a persistent ordered gateway collection with exactly one active entry."""

    active_gateway_id: str | None
    gateways: tuple[ModelGateway, ...]

    def __post_init__(self) -> None:
        identifiers = tuple(gateway.gateway_id for gateway in self.gateways)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("model gateway identifiers must be unique")
        if bool(self.gateways) != (self.active_gateway_id is not None):
            raise ValueError("a non-empty model gateway catalog must have an active gateway")
        if self.active_gateway_id is not None and self.active_gateway_id not in identifiers:
            raise ValueError("active model gateway does not exist")

    @property
    def active_gateway(self) -> ModelGateway | None:
        """Return the selected gateway, or ``None`` for an empty catalog."""

        return next(
            (
                gateway
                for gateway in self.gateways
                if gateway.gateway_id == self.active_gateway_id
            ),
            None,
        )


class ModelProviderConfigPort(Protocol):
    """Persist model credentials without exposing storage details to callers."""

    async def load(self) -> ModelProviderConfig | None:
        """Return the current configuration or ``None`` when it has not been supplied."""

        raise NotImplementedError

    async def save(self, config: ModelProviderConfig) -> None:
        """Atomically replace the current provider configuration."""

        raise NotImplementedError


class ModelGatewayStorePort(ModelProviderConfigPort, Protocol):
    """Persist the complete gateway catalog behind the Secret Store boundary."""

    async def load_catalog(self) -> ModelGatewayCatalog:
        """Return every gateway including credentials only to trusted application code."""

        raise NotImplementedError

    async def save_catalog(self, catalog: ModelGatewayCatalog) -> None:
        """Atomically replace the complete validated gateway catalog."""

        raise NotImplementedError

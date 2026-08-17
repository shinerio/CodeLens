from dataclasses import dataclass
from typing import Literal, Protocol

type GatewayApiType = Literal["responses", "chat_completions"]
type ModelProviderVendor = Literal["openai", "deepseek", "zhipu", "qwen"]
type ThinkingLevel = Literal["disabled", "low", "medium", "high"]
_DEFAULT_API_TYPE: GatewayApiType = "chat_completions"
_DEFAULT_MAX_TOKENS: int = 65536
_DEFAULT_THINKING_LEVEL: ThinkingLevel = "disabled"
_DEFAULT_AGENT_TIMEOUT: int = 3600
_DEFAULT_MAX_AGENT_TURNS: int = 500
_DEFAULT_MAX_TOOL_CALLS: int = 500
_DEFAULT_MAX_IDENTICAL_TOOL_RESULTS: int = 3
_DEFAULT_TOOL_TIMEOUT_SECONDS: int = 30
_DEFAULT_MAX_RETRIES: int = 10
_DEFAULT_RETRY_BACKOFF_BASE: float = 1.0
_DEFAULT_RETRY_MAX_DELAY: float = 30.0
_DEFAULT_NO_PROGRESS_ROUNDS_THRESHOLD: int = 10
_MIN_MAX_RETRIES: int = 0
_MAX_MAX_RETRIES: int = 10
_MIN_RETRY_BACKOFF_BASE: float = 0.1
_MAX_RETRY_BACKOFF_BASE: float = 60.0
_MIN_RETRY_MAX_DELAY: float = 1.0
_MAX_RETRY_MAX_DELAY: float = 300.0
_MIN_NO_PROGRESS_ROUNDS_THRESHOLD: int = 1
_MAX_NO_PROGRESS_ROUNDS_THRESHOLD: int = 100


@dataclass(frozen=True)
class ModelProviderConfig:
    """Hold one provider credential only inside the Secret Store boundary."""

    api_key: str
    model: str
    base_url: str
    vendor: ModelProviderVendor = "openai"
    api_type: GatewayApiType = _DEFAULT_API_TYPE
    max_tokens: int = _DEFAULT_MAX_TOKENS
    thinking_level: ThinkingLevel = _DEFAULT_THINKING_LEVEL
    agent_timeout: int = _DEFAULT_AGENT_TIMEOUT
    max_agent_turns: int = _DEFAULT_MAX_AGENT_TURNS
    max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS
    max_identical_tool_results: int = _DEFAULT_MAX_IDENTICAL_TOOL_RESULTS
    tool_timeout_seconds: int = _DEFAULT_TOOL_TIMEOUT_SECONDS
    max_retries: int = _DEFAULT_MAX_RETRIES
    retry_backoff_base: float = _DEFAULT_RETRY_BACKOFF_BASE
    retry_max_delay: float = _DEFAULT_RETRY_MAX_DELAY
    no_progress_rounds_threshold: int = _DEFAULT_NO_PROGRESS_ROUNDS_THRESHOLD

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_retries, bool)
            or self.max_retries < _MIN_MAX_RETRIES
            or self.max_retries > _MAX_MAX_RETRIES
        ):
            raise ValueError("max_retries must be between 0 and 10")
        if (
            self.retry_backoff_base < _MIN_RETRY_BACKOFF_BASE
            or self.retry_backoff_base > _MAX_RETRY_BACKOFF_BASE
        ):
            raise ValueError("retry_backoff_base must be between 0.1 and 60.0")
        if (
            self.retry_max_delay < _MIN_RETRY_MAX_DELAY
            or self.retry_max_delay > _MAX_RETRY_MAX_DELAY
        ):
            raise ValueError("retry_max_delay must be between 1.0 and 300.0")
        if (
            isinstance(self.no_progress_rounds_threshold, bool)
            or self.no_progress_rounds_threshold < _MIN_NO_PROGRESS_ROUNDS_THRESHOLD
            or self.no_progress_rounds_threshold > _MAX_NO_PROGRESS_ROUNDS_THRESHOLD
        ):
            raise ValueError("no_progress_rounds_threshold must be between 1 and 100")


@dataclass(frozen=True)
class ModelGateway:
    """Describe one named OpenAI-compatible gateway including its write-only credential."""

    gateway_id: str
    name: str
    api_key: str
    model: str
    base_url: str
    vendor: ModelProviderVendor = "openai"
    api_type: GatewayApiType = _DEFAULT_API_TYPE
    max_tokens: int = _DEFAULT_MAX_TOKENS
    thinking_level: ThinkingLevel = _DEFAULT_THINKING_LEVEL
    agent_timeout: int = _DEFAULT_AGENT_TIMEOUT
    max_agent_turns: int = _DEFAULT_MAX_AGENT_TURNS
    max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS
    max_identical_tool_results: int = _DEFAULT_MAX_IDENTICAL_TOOL_RESULTS
    tool_timeout_seconds: int = _DEFAULT_TOOL_TIMEOUT_SECONDS
    max_retries: int = _DEFAULT_MAX_RETRIES
    retry_backoff_base: float = _DEFAULT_RETRY_BACKOFF_BASE
    retry_max_delay: float = _DEFAULT_RETRY_MAX_DELAY
    no_progress_rounds_threshold: int = _DEFAULT_NO_PROGRESS_ROUNDS_THRESHOLD

    @property
    def provider_config(self) -> ModelProviderConfig:
        """Return the active runtime view without gateway-management metadata."""

        return ModelProviderConfig(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            vendor=self.vendor,
            api_type=self.api_type,
            max_tokens=self.max_tokens,
            thinking_level=self.thinking_level,
            agent_timeout=self.agent_timeout,
            max_agent_turns=self.max_agent_turns,
            max_tool_calls=self.max_tool_calls,
            max_identical_tool_results=self.max_identical_tool_results,
            tool_timeout_seconds=self.tool_timeout_seconds,
            max_retries=self.max_retries,
            retry_backoff_base=self.retry_backoff_base,
            retry_max_delay=self.retry_max_delay,
            no_progress_rounds_threshold=self.no_progress_rounds_threshold,
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
            (gateway for gateway in self.gateways if gateway.gateway_id == self.active_gateway_id),
            None,
        )


@dataclass(frozen=True)
class GatewayConnectivityResult:
    """Report TCP reachability of a gateway base URL without exposing credentials."""

    ok: bool
    latency_ms: int | None
    detail: str


@dataclass(frozen=True)
class GatewayAvailabilityResult:
    """Report whether the LLM endpoint responds to a minimal ping."""

    ok: bool
    latency_ms: int | None
    detail: str


class ModelGatewayProbePort(Protocol):
    """Test gateway reachability without persisting changes or logging secrets."""

    async def test_connectivity(self, base_url: str) -> GatewayConnectivityResult:
        """Attempt a TCP connection to the host and port parsed from ``base_url``."""

        raise NotImplementedError

    async def test_availability(self, config: ModelProviderConfig) -> GatewayAvailabilityResult:
        """Send a minimal chat completion to verify the LLM can respond."""

        raise NotImplementedError


class ModelProviderConfigPort(Protocol):
    """Load the active model gateway without exposing storage details to callers."""

    async def load(self) -> ModelProviderConfig | None:
        """Return the current configuration or ``None`` when it has not been supplied."""

        raise NotImplementedError


class ModelGatewayStorePort(ModelProviderConfigPort, Protocol):
    """Persist the complete gateway catalog behind the Secret Store boundary."""

    async def load_catalog(self) -> ModelGatewayCatalog:
        """Return every gateway including credentials only to trusted application code."""

        raise NotImplementedError

    async def save_catalog(self, catalog: ModelGatewayCatalog) -> None:
        """Atomically replace the complete validated gateway catalog."""

        raise NotImplementedError

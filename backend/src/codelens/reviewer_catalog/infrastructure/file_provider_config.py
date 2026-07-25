import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict, cast

from codelens.reviewer_catalog.domain.provider_config import (
    _DEFAULT_AGENT_TIMEOUT,
    _DEFAULT_API_TYPE,
    _DEFAULT_MAX_TOKENS,
    _DEFAULT_THINKING_LEVEL,
    GatewayApiType,
    ModelGateway,
    ModelGatewayCatalog,
    ModelProviderConfig,
    ModelProviderVendor,
    ThinkingLevel,
)


class _StoredProviderConfig(TypedDict):
    api_key: str
    model: str
    base_url: str


class _StoredGateway(_StoredProviderConfig):
    gateway_id: str
    name: str
    max_tokens: int
    thinking_level: str
    agent_timeout: int
    vendor: ModelProviderVendor
    api_type: GatewayApiType


class _StoredGatewayCatalog(TypedDict):
    version: int
    active_gateway_id: str | None
    gateways: list[_StoredGateway]


class FilesystemModelProviderConfigAdapter:
    """Persist multiple gateway secrets in one owner-only atomic catalog file."""

    def __init__(self, data_dir: Path) -> None:
        self._directory = data_dir.expanduser().resolve() / "secrets"
        self._path = self._directory / "model-gateways.json"

    async def load(self) -> ModelProviderConfig | None:
        """Load the currently active provider without logging secret contents."""

        catalog = await self.load_catalog()
        gateway = catalog.active_gateway
        return gateway.provider_config if gateway is not None else None

    async def load_catalog(self) -> ModelGatewayCatalog:
        """Load and validate the complete gateway catalog off the event loop."""

        return await asyncio.to_thread(self._load_catalog_sync)

    async def save_catalog(self, catalog: ModelGatewayCatalog) -> None:
        """Atomically write the gateway catalog with owner-only permissions."""

        await asyncio.to_thread(self._save_catalog_sync, catalog)

    def _load_catalog_sync(self) -> ModelGatewayCatalog:
        if self._path.is_file():
            return self._parse_catalog(self._read_json(self._path))
        return ModelGatewayCatalog(None, ())

    @staticmethod
    def _read_json(path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def _parse_catalog(cls, payload: object) -> ModelGatewayCatalog:
        if not isinstance(payload, dict):
            raise ValueError("model gateway catalog is invalid")
        if set(payload) != {"version", "active_gateway_id", "gateways"}:
            raise ValueError("model gateway catalog is invalid")
        if payload["version"] != 1 or not isinstance(payload["gateways"], list):
            raise ValueError("model gateway catalog is invalid")
        active_gateway_id = payload["active_gateway_id"]
        if active_gateway_id is not None and not isinstance(active_gateway_id, str):
            raise ValueError("model gateway catalog is invalid")
        gateways: list[ModelGateway] = []
        required_keys = {"gateway_id", "name", "api_key", "model", "base_url"}
        for item in payload["gateways"]:
            if not isinstance(item, dict) or not required_keys.issubset(item):
                raise ValueError("model gateway catalog is invalid")
            if any(
                not isinstance(item[key], str) or not item[key].strip() for key in required_keys
            ):
                raise ValueError("model gateway catalog is invalid")
            raw_api_type = item.get("api_type", _DEFAULT_API_TYPE)
            raw_vendor = item.get("vendor", "openai")
            if raw_vendor not in ("openai", "deepseek", "zhipu"):
                raise ValueError("model gateway catalog is invalid")
            if raw_api_type not in ("responses", "chat_completions"):
                raise ValueError("model gateway catalog is invalid")
            raw_max_tokens = item.get("max_tokens", _DEFAULT_MAX_TOKENS)
            if not isinstance(raw_max_tokens, int) or isinstance(raw_max_tokens, bool):
                raise ValueError("model gateway catalog is invalid")
            raw_thinking_level = item.get("thinking_level", _DEFAULT_THINKING_LEVEL)
            if not isinstance(raw_thinking_level, str) or raw_thinking_level not in (
                "disabled",
                "low",
                "medium",
                "high",
            ):
                raise ValueError("model gateway catalog is invalid")
            raw_agent_timeout = item.get("agent_timeout", _DEFAULT_AGENT_TIMEOUT)
            if not isinstance(raw_agent_timeout, int) or isinstance(raw_agent_timeout, bool):
                raise ValueError("model gateway catalog is invalid")
            gateways.append(
                ModelGateway(
                    gateway_id=cast(str, item["gateway_id"]),
                    name=cast(str, item["name"]),
                    api_key=cast(str, item["api_key"]),
                    model=cast(str, item["model"]),
                    base_url=cast(str, item["base_url"]),
                    vendor=cast(ModelProviderVendor, raw_vendor),
                    api_type=cast(GatewayApiType, raw_api_type),
                    max_tokens=raw_max_tokens,
                    thinking_level=cast(ThinkingLevel, raw_thinking_level),
                    agent_timeout=raw_agent_timeout,
                )
            )
        return ModelGatewayCatalog(cast(str | None, active_gateway_id), tuple(gateways))

    def _save_catalog_sync(self, catalog: ModelGatewayCatalog) -> None:
        # Windows: mkdir mode is ignored; chmod works but has different semantics.
        # Unix-like systems (Linux, macOS): enforce strict owner-only permissions.
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._directory, 0o700)
        payload: _StoredGatewayCatalog = {
            "version": 1,
            "active_gateway_id": catalog.active_gateway_id,
            "gateways": [
                {
                    "gateway_id": gateway.gateway_id,
                    "name": gateway.name,
                    "api_key": gateway.api_key,
                    "model": gateway.model,
                    "base_url": gateway.base_url,
                    "vendor": gateway.vendor,
                    "api_type": gateway.api_type,
                    "max_tokens": gateway.max_tokens,
                    "thinking_level": gateway.thinking_level,
                    "agent_timeout": gateway.agent_timeout,
                }
                for gateway in catalog.gateways
            ],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._directory,
            prefix=".model-gateways-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            # os.fchmod is Unix-only; Windows skips file descriptor permissions.
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
            os.chmod(self._path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

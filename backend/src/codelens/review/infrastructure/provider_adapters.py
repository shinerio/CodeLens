"""Translate vendor configuration into isolated OpenAI Agents SDK request behavior."""

from dataclasses import dataclass
from typing import Protocol

from agents.model_settings import ModelSettings, Reasoning
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel

from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfig


@dataclass(frozen=True)
class ProviderRequestBehavior:
    """Describe the SDK model transport and settings for a configured vendor."""

    model_class: type[OpenAIResponsesModel] | type[OpenAIChatCompletionsModel]
    model_settings: ModelSettings


class ModelProviderAdapter(Protocol):
    """Own one vendor's protocol-specific request translation."""

    vendor: str

    def request_behavior(self, config: ModelProviderConfig) -> ProviderRequestBehavior: ...


class OpenAIProviderAdapter:
    vendor = "openai"

    def request_behavior(self, config: ModelProviderConfig) -> ProviderRequestBehavior:
        return ProviderRequestBehavior(
            OpenAIResponsesModel if config.api_type == "responses" else OpenAIChatCompletionsModel,
            ModelSettings(
                max_tokens=config.max_tokens,
                reasoning=(
                    None
                    if config.thinking_level == "disabled"
                    else Reasoning(effort=config.thinking_level)
                ),
            ),
        )


class DeepSeekProviderAdapter:
    """Follow DeepSeek's documented Chat Completions thinking controls."""

    vendor = "deepseek"

    def request_behavior(self, config: ModelProviderConfig) -> ProviderRequestBehavior:
        enabled = config.thinking_level != "disabled"
        return ProviderRequestBehavior(
            OpenAIChatCompletionsModel,
            ModelSettings(
                max_tokens=config.max_tokens,
                reasoning=Reasoning(effort="high") if enabled else None,
                extra_body={"thinking": {"type": "enabled" if enabled else "disabled"}},
            ),
        )


class ModelProviderAdapterRegistry:
    """Open registry so new vendors do not change review orchestration."""

    def __init__(self, adapters: tuple[ModelProviderAdapter, ...] | None = None) -> None:
        resolved = adapters or (OpenAIProviderAdapter(), DeepSeekProviderAdapter())
        self._adapters = {adapter.vendor: adapter for adapter in resolved}

    def resolve(self, vendor: str) -> ModelProviderAdapter:
        try:
            return self._adapters[vendor]
        except KeyError as error:
            raise ValueError("configured model provider is unsupported") from error

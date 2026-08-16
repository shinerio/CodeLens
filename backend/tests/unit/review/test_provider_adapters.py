from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel

from codelens.review.infrastructure.provider_adapters import ModelProviderAdapterRegistry
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfig


def _config(
    *, vendor: str, api_type: str = "responses", thinking: str = "disabled"
) -> ModelProviderConfig:
    return ModelProviderConfig(
        api_key="test-key",
        model="test-model",
        base_url="https://gateway.example/v1",
        vendor=vendor,  # type: ignore[arg-type]
        api_type=api_type,  # type: ignore[arg-type]
        thinking_level=thinking,  # type: ignore[arg-type]
    )


def test_openai_adapter_uses_selected_transport_and_omits_disabled_reasoning() -> None:
    behavior = (
        ModelProviderAdapterRegistry().resolve("openai").request_behavior(_config(vendor="openai"))
    )

    assert behavior.model_class is OpenAIResponsesModel
    assert behavior.model_settings.reasoning is None
    assert behavior.model_settings.extra_body is None


def test_deepseek_adapter_uses_chat_and_documented_thinking_extension() -> None:
    behavior = (
        ModelProviderAdapterRegistry()
        .resolve("deepseek")
        .request_behavior(_config(vendor="deepseek"))
    )

    assert behavior.model_class is OpenAIChatCompletionsModel
    assert behavior.model_settings.reasoning is None
    assert behavior.model_settings.extra_body == {"thinking": {"type": "disabled"}}


def test_zhipu_adapter_uses_chat_and_disables_thinking_via_extra_body() -> None:
    behavior = (
        ModelProviderAdapterRegistry().resolve("zhipu").request_behavior(_config(vendor="zhipu"))
    )

    assert behavior.model_class is OpenAIChatCompletionsModel
    assert behavior.model_settings.reasoning is None
    assert behavior.model_settings.extra_body == {"thinking": {"type": "disabled"}}


def test_zhipu_adapter_enables_thinking_with_configured_effort() -> None:
    behavior = (
        ModelProviderAdapterRegistry()
        .resolve("zhipu")
        .request_behavior(_config(vendor="zhipu", thinking="low"))
    )

    assert behavior.model_class is OpenAIChatCompletionsModel
    assert behavior.model_settings.reasoning is not None
    assert behavior.model_settings.reasoning.effort == "low"
    assert behavior.model_settings.extra_body == {"thinking": {"type": "enabled"}}


def test_qwen_adapter_uses_chat_and_disables_thinking_via_extra_body() -> None:
    behavior = (
        ModelProviderAdapterRegistry().resolve("qwen").request_behavior(_config(vendor="qwen"))
    )

    assert behavior.model_class is OpenAIChatCompletionsModel
    assert behavior.model_settings.reasoning is None
    assert behavior.model_settings.extra_body == {"enable_thinking": False}


def test_qwen_adapter_enables_thinking_with_configured_effort() -> None:
    behavior = (
        ModelProviderAdapterRegistry()
        .resolve("qwen")
        .request_behavior(_config(vendor="qwen", thinking="high"))
    )

    assert behavior.model_class is OpenAIChatCompletionsModel
    assert behavior.model_settings.reasoning is not None
    assert behavior.model_settings.reasoning.effort == "high"
    assert behavior.model_settings.extra_body == {"enable_thinking": True}

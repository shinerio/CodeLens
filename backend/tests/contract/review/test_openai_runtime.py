import logging
import os
import traceback
from dataclasses import dataclass

import httpx
import pytest
from agents import Agent, RunConfig
from agents.exceptions import ModelBehaviorError
from agents.models.openai_responses import OpenAIResponsesModel
from openai import APIConnectionError, InternalServerError, RateLimitError

from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.findings.infrastructure.model_output import FindingBatchSchema
from codelens.review.domain.errors import (
    PermanentAgentOutputError,
    TransientAgentRuntimeError,
)
from codelens.review.infrastructure.openai_runtime import OpenAIAgentRuntime
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfig
from codelens.reviewer_catalog.infrastructure.builtin_agents import correctness_agent
from codelens.workspace.domain.models import ReviewMode


@dataclass(frozen=True)
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class FakeResponse:
    response_id: str | None
    request_id: str | None
    usage: FakeUsage
    output: tuple[object, ...]


@dataclass(frozen=True)
class FakeResult:
    final_output: object
    raw_responses: tuple[FakeResponse, ...]


class FakeRunner:
    def __init__(self, result: FakeResult | Exception) -> None:
        self.result = result
        self.starting_agent: Agent[None] | None = None
        self.input_payload: str | None = None
        self.max_turns: int | None = None
        self.run_config: RunConfig | None = None

    async def run(
        self,
        starting_agent: Agent[None],
        input: str,
        *,
        max_turns: int,
        run_config: RunConfig,
    ) -> FakeResult:
        self.starting_agent = starting_agent
        self.input_payload = input
        self.max_turns = max_turns
        self.run_config = run_config
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StaticProviderConfigStore:
    def __init__(self, config: ModelProviderConfig | None = None) -> None:
        self.config = config

    async def load(self) -> ModelProviderConfig | None:
        return self.config

    async def save(self, config: ModelProviderConfig) -> None:
        self.config = config


def _provider_config() -> ModelProviderConfig:
    return ModelProviderConfig(
        api_key="sk-contract-secret",
        model="gpt-5.1",
        base_url="http://model-gateway.example:8080",
    )


def _agent() -> AgentVersion:
    return AgentVersion(
        agent_id="correctness",
        version=1,
        prompt_template="PROMPT_SECRET: inspect the bounded Snapshot input.",
        model_profile_id="balanced",
        output_schema_version="1",
        timeout_seconds=30.0,
        max_turns=3,
        token_budget=8_000,
        confidence_floor=0.7,
        failure_policy="fail_task",
        mode_support=(ReviewMode.REVIEW,),
        content_hash="a" * 64,
    )


async def test_uses_typed_public_sdk_contract_and_returns_redacted_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider_secret = "FULL_PROVIDER_PAYLOAD_SECRET"
    runner = FakeRunner(
        FakeResult(
            final_output=FindingBatchSchema(schema_version="1", findings=()),
            raw_responses=(
                FakeResponse("resp_1", "req_1", FakeUsage(12, 4), (provider_secret,)),
                FakeResponse("resp_2", "req_2", FakeUsage(8, 3), (provider_secret,)),
            ),
        )
    )
    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(_provider_config()),
        output_codec=AgentOutputCodec("1"),
        runner=runner,
    )
    source_secret = "SOURCE_BODY_SECRET"

    with caplog.at_level(logging.DEBUG):
        output = await runtime.invoke(_agent(), source_secret.encode())

    sdk_agent = runner.starting_agent
    assert sdk_agent is not None
    assert sdk_agent.output_type is FindingBatchSchema
    assert sdk_agent.instructions == _agent().prompt_template
    assert isinstance(sdk_agent.model, OpenAIResponsesModel)
    assert sdk_agent.model.model == "gpt-5.1"
    assert str(sdk_agent.model._client.base_url) == "http://model-gateway.example:8080"
    assert runner.input_payload == source_secret
    assert runner.max_turns == 3
    assert runner.run_config is not None
    assert runner.run_config.trace_include_sensitive_data is False
    assert output.canonical_bytes == b'{"findings":[],"schema_version":"1"}'
    assert output.response_ids == ("resp_1", "resp_2")
    assert output.model_name == "gpt-5.1"
    assert (output.input_tokens, output.output_tokens) == (20, 7)
    assert [diagnostic.request_id for diagnostic in output.diagnostics] == ["req_1", "req_2"]
    assert [diagnostic.output_item_count for diagnostic in output.diagnostics] == [1, 1]
    assert provider_secret not in repr(output.diagnostics)
    assert _agent().prompt_template not in caplog.text
    assert source_secret not in caplog.text
    assert provider_secret not in caplog.text
    assert "sk-contract-secret" not in caplog.text
    assert os.environ["OPENAI_AGENTS_DONT_LOG_MODEL_DATA"] == "1"
    assert os.environ["OPENAI_AGENTS_DONT_LOG_TOOL_DATA"] == "1"


@pytest.mark.parametrize(
    "failure",
    [
        APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
        RateLimitError(
            "rate limited",
            response=httpx.Response(
                429,
                request=httpx.Request("POST", "https://api.openai.com"),
            ),
            body=None,
        ),
        InternalServerError(
            "server failed",
            response=httpx.Response(
                500,
                request=httpx.Request("POST", "https://api.openai.com"),
            ),
            body=None,
        ),
    ],
)
async def test_maps_retryable_provider_failures_without_leaking_details(failure: Exception) -> None:
    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(_provider_config()),
        output_codec=AgentOutputCodec("1"),
        runner=FakeRunner(failure),
    )

    with pytest.raises(TransientAgentRuntimeError) as captured:
        await runtime.invoke(_agent(), b"bounded input")

    assert "rate limited" not in str(captured.value)
    assert "server failed" not in str(captured.value)


@pytest.mark.parametrize(
    "result",
    [
        FakeResult({"schema_version": "1", "findings": "not-a-list"}, ()),
        ModelBehaviorError("FULL_PROVIDER_PAYLOAD_SECRET"),
    ],
)
async def test_maps_invalid_output_to_a_permanent_failure(result: FakeResult | Exception) -> None:
    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(_provider_config()),
        output_codec=AgentOutputCodec("1"),
        runner=FakeRunner(result),
    )

    with pytest.raises(PermanentAgentOutputError) as captured:
        await runtime.invoke(_agent(), b"bounded input")

    assert "FULL_PROVIDER_PAYLOAD_SECRET" not in str(captured.value)
    formatted = "".join(traceback.format_exception(captured.value))
    assert "FULL_PROVIDER_PAYLOAD_SECRET" not in formatted
    assert captured.value.__context__ is None


async def test_missing_provider_configuration_fails_only_when_invoked() -> None:
    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(),
        output_codec=AgentOutputCodec("1"),
        runner=FakeRunner(FakeResult({}, ())),
    )

    with pytest.raises(PermanentAgentOutputError, match="not configured"):
        await runtime.invoke(_agent(), b"bounded input")


def test_builtin_correctness_agent_is_immutable_and_content_addressed() -> None:
    first = correctness_agent()
    second = correctness_agent()

    assert first == second
    assert first.agent_id == "correctness"
    assert first.output_schema_version == "1"
    assert ReviewMode.REVIEW in first.mode_support
    assert len(first.content_hash) == 64

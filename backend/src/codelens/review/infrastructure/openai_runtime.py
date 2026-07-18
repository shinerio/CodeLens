# ruff: noqa: E402

import asyncio
import os
from typing import Literal, Protocol, cast

os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "1")
os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "1")

from agents import Agent, RunConfig, Runner
from agents.exceptions import (
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
    UserError,
)
from agents.models.openai_responses import OpenAIResponsesModel
from agents.result import RunResult
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from codelens.review.domain.errors import (
    PermanentAgentOutputError,
    TransientAgentRuntimeError,
)
from codelens.review.domain.ports import (
    AgentOutputCodecPort,
    AgentResponseDiagnostic,
    UnvalidatedAgentOutput,
)
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfigPort


class _RunnerPort(Protocol):
    async def run(
        self,
        starting_agent: Agent[None],
        input: str,
        *,
        max_turns: int,
        run_config: RunConfig,
    ) -> object:
        raise NotImplementedError


class _PublicSdkRunner:
    async def run(
        self,
        starting_agent: Agent[None],
        input: str,
        *,
        max_turns: int,
        run_config: RunConfig,
    ) -> object:
        return await Runner.run(
            starting_agent=starting_agent,
            input=input,
            max_turns=max_turns,
            run_config=run_config,
        )


class OpenAIAgentRuntime:
    """Adapt the public Agents SDK to the provider-neutral runtime port."""

    def __init__(
        self,
        config_store: ModelProviderConfigPort,
        output_codec: AgentOutputCodecPort,
        runner: _RunnerPort | None = None,
    ) -> None:
        self._config_store = config_store
        self._output_codec = output_codec
        self._runner = runner or _PublicSdkRunner()

    async def invoke(
        self,
        agent: AgentVersion,
        input_payload: bytes,
    ) -> UnvalidatedAgentOutput:
        provider_config = await self._config_store.load()
        if provider_config is None:
            raise PermanentAgentOutputError("Model provider is not configured")
        input_text: str | None = None
        try:
            input_text = input_payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        if input_text is None:
            raise PermanentAgentOutputError("Agent input is not valid UTF-8") from None
        if agent.output_schema_version != self._output_codec.schema_version:
            raise PermanentAgentOutputError("Agent output contract is unsupported")

        client = AsyncOpenAI(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
        )
        sdk_agent: Agent[None] = Agent(
            name=f"{agent.agent_id}:v{agent.version}",
            instructions=agent.prompt_template,
            model=OpenAIResponsesModel(
                model=provider_config.model,
                openai_client=client,
            ),
            output_type=self._output_codec.output_type,
        )
        run_config = RunConfig(trace_include_sensitive_data=False)
        failure_kind: Literal["transient", "permanent"] | None = None
        raw_result: object | None = None
        try:
            try:
                async with asyncio.timeout(agent.timeout_seconds):
                    raw_result = await self._runner.run(
                        sdk_agent,
                        input_text,
                        max_turns=agent.max_turns,
                        run_config=run_config,
                    )
            except (
                TimeoutError,
                APIConnectionError,
                APITimeoutError,
                RateLimitError,
                InternalServerError,
            ):
                failure_kind = "transient"
            except APIStatusError as provider_error:
                failure_kind = "transient" if provider_error.status_code >= 500 else "permanent"
            except (
                MaxTurnsExceeded,
                ModelBehaviorError,
                ModelRefusalError,
                UserError,
            ):
                failure_kind = "permanent"
        finally:
            await client.close()

        if failure_kind == "transient":
            raise TransientAgentRuntimeError("Agent provider request can be retried") from None
        if failure_kind == "permanent" or raw_result is None:
            raise PermanentAgentOutputError("Agent returned unusable output") from None

        result = cast(RunResult, raw_result)
        canonical_bytes: bytes | None = None
        try:
            canonical_bytes = self._output_codec.encode(result.final_output)
        except ValueError:
            pass
        if canonical_bytes is None:
            raise PermanentAgentOutputError("Agent returned unusable output") from None

        diagnostics = tuple(
            AgentResponseDiagnostic(
                response_id=response.response_id,
                request_id=response.request_id,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                output_item_count=len(response.output),
            )
            for response in result.raw_responses
        )
        return UnvalidatedAgentOutput(
            canonical_bytes=canonical_bytes,
            response_ids=tuple(
                diagnostic.response_id
                for diagnostic in diagnostics
                if diagnostic.response_id is not None
            ),
            model_name=provider_config.model,
            input_tokens=sum(item.input_tokens for item in diagnostics),
            output_tokens=sum(item.output_tokens for item in diagnostics),
            diagnostics=diagnostics,
        )

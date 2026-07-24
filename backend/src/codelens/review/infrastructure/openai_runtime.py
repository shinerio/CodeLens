# ruff: noqa: E402

import asyncio
import json
import logging
import os
from typing import Any, Literal, Protocol, cast

os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "1")
os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "1")

from agents import Agent, RawResponsesStreamEvent, RunConfig, RunItemStreamEvent, Runner
from agents.exceptions import (
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
    UserError,
)
from agents.result import RunResult
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from codelens.review.application.i18n_prompt_loader import I18nPromptLoaderPort
from codelens.review.domain.errors import (
    AgentMaxTurnsExceededError,
    PermanentAgentOutputError,
    TransientAgentRuntimeError,
)
from codelens.review.domain.ports import (
    AgentOutputCodecPort,
    AgentResponseDiagnostic,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    UnvalidatedAgentOutput,
)
from codelens.review.infrastructure.comment_collector import ReviewCommentCollector
from codelens.review.infrastructure.provider_adapters import ModelProviderAdapterRegistry
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfigPort
from codelens.workspace.domain.models import ReviewSnapshot
from codelens.workspace.infrastructure.git_cli import GitCli

type _AgentFailure = (
    AgentMaxTurnsExceededError | TransientAgentRuntimeError | PermanentAgentOutputError
)
_LOGGER = logging.getLogger(__name__)


class _RunnerPort(Protocol):
    async def run(
        self,
        starting_agent: Agent[None],
        input: Any,
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

    def run_streamed(
        self,
        starting_agent: Agent[None],
        input: Any,
        *,
        max_turns: int,
        run_config: RunConfig,
    ) -> object:
        return Runner.run_streamed(
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
        git: GitCli,
        prompt_loader: I18nPromptLoaderPort,
        runner: _RunnerPort | None = None,
    ) -> None:
        self._config_store = config_store
        self._output_codec = output_codec
        self._git = git
        self._prompt_loader = prompt_loader
        self._runner = runner or _PublicSdkRunner()

    async def invoke(
        self,
        agent: AgentVersion,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
    ) -> UnvalidatedAgentOutput:
        return await self._invoke(agent, input_payload, snapshot, sink=None)

    async def invoke_stream(
        self,
        agent: AgentVersion,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        sink: AgentRuntimeEventSink,
    ) -> UnvalidatedAgentOutput:
        """Emit visible model text and tool evidence while preserving the final checkpoint."""

        return await self._invoke(agent, input_payload, snapshot, sink=sink)

    async def _invoke(
        self,
        agent: AgentVersion,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        sink: AgentRuntimeEventSink | None,
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
        prompts = self._prompt_loader.get(self._output_locale(input_text))
        if agent.output_contract_version != self._output_codec.schema_version:
            raise PermanentAgentOutputError("Agent output contract is unsupported")

        client = AsyncOpenAI(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
        )
        behavior = (
            ModelProviderAdapterRegistry()
            .resolve(provider_config.vendor)
            .request_behavior(provider_config)
        )
        snapshot_tools = FilesystemReviewTools(snapshot, self._git, max_tool_calls=None)
        comment_collector = ReviewCommentCollector(
            snapshot=snapshot,
            reviewer_id=agent.agent_id,
            confidence_floor=agent.confidence_floor,
            tools=snapshot_tools,
            output_locale="zh-CN" if prompts.locale == "zh-CN" else "en",
            tool_descriptions={name: tool.description for name, tool in prompts.tools.items()},
            language_error=prompts.comment_language_error,
        )
        investigation_agent: Agent[None] = Agent(
            name=f"{agent.agent_id}:v{agent.version}",
            instructions="\n\n".join(
                (
                    prompts.platform_policy,
                    prompts.repository_instruction_policy,
                    prompts.runtime_instruction,
                    prompts.output_contract,
                    f"# Reviewer Policy\n{agent.prompt_template}",
                )
            ),
            model=behavior.model_class(
                model=provider_config.model,
                openai_client=client,
            ),
            model_settings=behavior.model_settings,
            tools=[
                *snapshot_tools.as_agent_tools(
                    {name: tool.description for name, tool in prompts.tools.items()}
                ),
                *comment_collector.as_agent_tools(),
            ],
        )
        run_config = RunConfig(trace_include_sensitive_data=False)
        investigation: object | None = None
        failure: _AgentFailure | None = None
        phase: Literal["investigation", "unknown"] = "investigation"
        try:
            try:
                investigation = await self._run_observable(
                    investigation_agent,
                    input_text,
                    agent.max_turns,
                    run_config,
                    sink,
                    timeout_seconds=provider_config.agent_timeout,
                )
            except APIStatusError as provider_error:
                failure = self._status_failure(provider_error, phase)
            except APITimeoutError:
                failure = self._failure(
                    phase, "provider_timeout", "provider timeout", retryable=True
                )
            except TimeoutError:
                failure = self._failure(
                    phase, "agent_run_timeout", "agent run timed out", retryable=True
                )
            except APIConnectionError:
                failure = self._failure(
                    phase, "provider_connection_error", "provider connection error", retryable=True
                )
            except RateLimitError:
                failure = self._failure(
                    phase, "provider_rate_limited", "provider rate limit", retryable=True
                )
            except InternalServerError:
                failure = self._failure(
                    phase, "provider_server_error", "provider server error", retryable=True
                )
            except MaxTurnsExceeded:
                failure = AgentMaxTurnsExceededError(
                    "Code investigation failed: model used all allowed turns.",
                    phase=phase,
                    reason_code="max_model_turns_exceeded",
                )
            except (ModelBehaviorError, ModelRefusalError, UserError) as model_error:
                _LOGGER.warning(
                    "Model produced invalid structured output",
                    extra={"phase": phase, "error": str(model_error)[:500]},
                )
                failure = self._failure(
                    phase, "invalid_model_output", "model returned unusable output", retryable=False
                )
        except BaseException:
            await client.close()
            raise

        if failure is not None:
            await client.close()
            raise failure from None
        if investigation is None:
            await client.close()
            raise self._failure(
                "investigation",
                "missing_model_output",
                "model returned no structured output",
                retryable=False,
            )

        result = cast(RunResult, investigation)
        try:
            canonical_bytes = self._output_codec.encode(comment_collector.finding_batch())
        except ValueError as error:
            await client.close()
            raise PermanentAgentOutputError(
                "Comment tool produced an invalid review output.",
                phase="investigation",
                reason_code="invalid_comment_output",
                retryable=False,
            ) from error

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
        output = UnvalidatedAgentOutput(
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
        await client.close()
        return output

    @staticmethod
    def _output_locale(input_text: str) -> Literal["en", "zh-CN"]:
        """Read the validated locale marker embedded in the canonical Agent input."""

        try:
            payload = json.loads(input_text)
        except json.JSONDecodeError:
            return "en"
        return "zh-CN" if payload.get("output_locale") == "zh-CN" else "en"

    @classmethod
    def _failure(
        cls,
        phase: Literal["investigation", "unknown"],
        reason_code: str,
        reason: str,
        *,
        retryable: bool,
        provider_status_code: int | None = None,
    ) -> TransientAgentRuntimeError | PermanentAgentOutputError:
        message = f"Code investigation failed: {reason}."
        if retryable:
            return TransientAgentRuntimeError(
                f"{message} Retry the review.",
                phase=phase,
                reason_code=reason_code,
                retryable=True,
                provider_status_code=provider_status_code,
            )
        return PermanentAgentOutputError(
            message,
            phase=phase,
            reason_code=reason_code,
            retryable=False,
            provider_status_code=provider_status_code,
        )

    @classmethod
    def _status_failure(
        cls, error: APIStatusError, phase: Literal["investigation", "unknown"]
    ) -> TransientAgentRuntimeError | PermanentAgentOutputError:
        status_code = error.status_code
        if status_code == 429:
            return cls._failure(
                phase,
                "provider_rate_limited",
                "provider rate limit",
                retryable=True,
                provider_status_code=status_code,
            )
        if status_code >= 500:
            return cls._failure(
                phase,
                "provider_server_error",
                "provider server error",
                retryable=True,
                provider_status_code=status_code,
            )
        return cls._failure(
            phase,
            "provider_request_rejected",
            "provider rejected the request",
            retryable=False,
            provider_status_code=status_code,
        )

    async def _run_observable(
        self,
        agent: Agent[None],
        input_value: Any,
        max_turns: int,
        run_config: RunConfig,
        sink: AgentRuntimeEventSink | None,
        *,
        timeout_seconds: int = 1800,
    ) -> object:
        if sink is None or not hasattr(self._runner, "run_streamed"):
            return await self._runner.run(
                agent,
                input_value,
                max_turns=max_turns,
                run_config=run_config,
            )
        await sink(AgentRuntimeEvent("model_started", "", {"agent_name": agent.name}))
        stream = cast(Any, self._runner).run_streamed(
            agent, input_value, max_turns=max_turns, run_config=run_config
        )
        async with asyncio.timeout(timeout_seconds):
            async for event in stream.stream_events():
                emitted = _visible_event(event)
                if emitted is not None:
                    await sink(emitted)
        await sink(AgentRuntimeEvent("model_completed", "", {"agent_name": agent.name}))
        return stream


def _visible_event(event: object) -> AgentRuntimeEvent | None:
    """Map streamed output and provider-issued reasoning summaries to console records."""

    if isinstance(event, RawResponsesStreamEvent):
        payload = event.data
        if getattr(payload, "type", "") == "response.output_text.delta":
            return AgentRuntimeEvent(
                "model_output_delta",
                str(getattr(payload, "delta", "")),
                _message_metadata(payload, "content_index"),
            )
        if getattr(payload, "type", "") == "response.output_text.done":
            return AgentRuntimeEvent(
                "model_output_completed", "", _message_metadata(payload, "content_index")
            )
        if getattr(payload, "type", "") == "response.reasoning_summary_text.delta":
            return AgentRuntimeEvent(
                "model_reasoning_delta",
                str(getattr(payload, "delta", "")),
                _message_metadata(payload, "summary_index"),
            )
        if getattr(payload, "type", "") == "response.reasoning_summary_text.done":
            return AgentRuntimeEvent(
                "model_reasoning_completed", "", _message_metadata(payload, "summary_index")
            )
        return None
    if isinstance(event, RunItemStreamEvent):
        if event.name == "tool_called":
            return AgentRuntimeEvent("tool_call", _json_value(event.item), {})
        if event.name == "tool_output":
            return AgentRuntimeEvent("tool_result", _json_value(event.item), {})
    return None


def _message_metadata(payload: object, index_name: str) -> dict[str, str]:
    """Return a stable per-content-part ID shared by stream deltas and completion events."""

    item_id = str(getattr(payload, "item_id", ""))
    index = str(getattr(payload, index_name, ""))
    return {"message_id": f"{item_id}:{index}"}


def _json_value(value: object) -> str:
    dump = getattr(value, "model_dump", None)
    payload = dump(mode="json") if callable(dump) else {"value": str(value)}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

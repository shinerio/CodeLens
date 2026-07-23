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

from codelens.review.domain.errors import (
    AgentMaxTurnsExceededError,
    PermanentAgentOutputError,
    TransientAgentRuntimeError,
)


class AgentOutputParseError(ValueError):
    """Raised when the model's finalizer output cannot be parsed as JSON."""

    def __init__(self, raw_output: str) -> None:
        self.raw_output = raw_output
        super().__init__("Could not parse model finalizer output as JSON")


from codelens.review.domain.ports import (
    AgentOutputCodecPort,
    AgentResponseDiagnostic,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    UnvalidatedAgentOutput,
)
from codelens.review.infrastructure.provider_adapters import ModelProviderAdapterRegistry
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfigPort
from codelens.workspace.domain.models import ReviewSnapshot
from codelens.workspace.infrastructure.git_cli import GitCli

type _AgentFailure = (
    AgentMaxTurnsExceededError | TransientAgentRuntimeError | PermanentAgentOutputError
)
_FINALIZER_MAX_ATTEMPTS = 3
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
        runner: _RunnerPort | None = None,
    ) -> None:
        self._config_store = config_store
        self._output_codec = output_codec
        self._git = git
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
        if agent.output_schema_version != self._output_codec.schema_version:
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
        finding_batch_schema = self._output_codec.json_schema()
        investigation_agent: Agent[None] = Agent(
            name=f"{agent.agent_id}:v{agent.version}",
            instructions=agent.prompt_template,
            model=behavior.model_class(
                model=provider_config.model,
                openai_client=client,
            ),
            model_settings=behavior.model_settings,
            tools=FilesystemReviewTools(snapshot, self._git, max_tool_calls=None).as_agent_tools(),
        )
        final_agent: Agent[None] = Agent(
            name=f"{agent.agent_id}:v{agent.version}:finalize",
            instructions=self._render_prompt(agent.finalization_prompt, finding_batch_schema),
            model=behavior.model_class(
                model=provider_config.model,
                openai_client=client,
            ),
            model_settings=behavior.model_settings,
            output_type=self._output_codec.output_type,
        )
        format_repair_agent: Agent[None] = Agent(
            name=f"{agent.agent_id}:v{agent.version}:format-repair",
            instructions=self._render_prompt(agent.format_repair_prompt, finding_batch_schema),
            model=behavior.model_class(model=provider_config.model, openai_client=client),
            model_settings=behavior.model_settings,
            output_type=self._output_codec.output_type,
        )
        run_config = RunConfig(trace_include_sensitive_data=False)
        raw_result: object | None = None
        failure: _AgentFailure | None = None
        phase: Literal["investigation", "finalizing", "unknown"] = "investigation"
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
                phase = "finalizing"
                raw_result = await self._run_finalizer(
                    final_agent,
                    self._finalizer_input(investigation),
                    run_config,
                    sink,
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
                    f"{self._phase_label(phase)} failed: model used all allowed turns.",
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
        finally:
            await client.close()

        if failure is not None:
            raise failure from None
        if raw_result is None:
            raise self._failure(
                "finalizing",
                "missing_model_output",
                "model returned no structured output",
                retryable=False,
            )

        result = cast(RunResult, raw_result)
        canonical_bytes: bytes | None = None
        output_failure: PermanentAgentOutputError | None = None
        try:
            canonical_bytes = self._output_codec.encode(self._finalizer_output(result.final_output))
        except AgentOutputParseError as parse_error:
            _LOGGER.warning(
                "Model output could not be parsed as JSON, saving raw output",
                extra={"phase": "finalizing", "output_preview": parse_error.raw_output[:500]},
            )
            if sink is not None:
                await sink(
                    AgentRuntimeEvent(
                        "model_raw_output",
                        parse_error.raw_output[:10000],
                        {"agent_name": agent.agent_id, "parse_failed": "true"},
                    )
                )
            output_failure = PermanentAgentOutputError(
                "Final structured output failed: model returned invalid JSON.",
                phase="finalizing",
                reason_code="invalid_structured_output",
                retryable=False,
            )
        except ValueError as encode_error:
            _LOGGER.warning(
                "Model output did not match FindingBatch schema, saving raw output",
                extra={
                    "phase": "finalizing",
                    "error": str(encode_error)[:500],
                    "output_type": type(result.final_output).__name__,
                },
            )
            raw_text = (
                result.final_output
                if isinstance(result.final_output, str)
                else str(result.final_output)
            )
            if sink is not None:
                await sink(
                    AgentRuntimeEvent(
                        "model_raw_output",
                        raw_text[:10000],
                        {"agent_name": agent.agent_id, "parse_failed": "true"},
                    )
                )
            output_failure = PermanentAgentOutputError(
                "Final structured output failed: model output did not match the review schema.",
                phase="finalizing",
                reason_code="invalid_structured_output",
                retryable=False,
            )

        if output_failure is not None:
            repaired = await self._run_finalizer(
                format_repair_agent,
                self._format_repair_input(result.final_output),
                run_config,
                sink,
            )
            try:
                repaired_result = cast(RunResult, repaired)
                canonical_bytes = self._output_codec.encode(
                    self._finalizer_output(repaired_result.final_output)
                )
            except (AgentOutputParseError, ValueError):
                canonical_bytes = None
            else:
                output_failure = None

        if output_failure is not None:
            raise output_failure
        if canonical_bytes is None:
            raise AssertionError("structured output encoding must return bytes or fail")

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

    async def _run_finalizer(
        self,
        agent: Agent[None],
        input_value: Any,
        run_config: RunConfig,
        sink: AgentRuntimeEventSink | None,
    ) -> object:
        """Retry only the final structured-output request after transient provider failures."""

        for attempt in range(1, _FINALIZER_MAX_ATTEMPTS + 1):
            try:
                return await self._run_observable(agent, input_value, 1, run_config, sink)
            except Exception as error:
                if (
                    not self._is_retryable_provider_error(error)
                    or attempt == _FINALIZER_MAX_ATTEMPTS
                ):
                    raise
                if sink is not None:
                    await sink(
                        AgentRuntimeEvent(
                            "lifecycle",
                            "Final structured output request failed temporarily; retrying.",
                            {
                                "agent_name": agent.name,
                                "attempt": str(attempt),
                                "max_attempts": str(_FINALIZER_MAX_ATTEMPTS),
                            },
                        )
                    )
                await asyncio.sleep(float(attempt))
        raise AssertionError("finalizer retry loop must return or raise")

    @staticmethod
    def _finalizer_input(investigation: object) -> Any:
        """Send controlled history so finalization can retain exact evidence identifiers."""

        history = getattr(investigation, "to_input_list", None)
        if callable(history):
            return history()
        conclusion = getattr(investigation, "final_output", None)
        if isinstance(conclusion, str) and conclusion.strip():
            return conclusion
        raise PermanentAgentOutputError(
            "Final structured output failed: investigation returned no usable evidence.",
            phase="finalizing",
            reason_code="missing_investigation_evidence",
            retryable=False,
        )

    @staticmethod
    def _render_prompt(template: str, schema: str) -> str:
        """Inject the runtime schema into a versioned prompt asset."""

        if "{{finding_batch_schema}}" not in template:
            raise PermanentAgentOutputError(
                "Final structured output failed: prompt template is invalid.",
                phase="finalizing",
                reason_code="invalid_finalizer_prompt",
                retryable=False,
            )
        return template.replace("{{finding_batch_schema}}", schema)

    @staticmethod
    def _format_repair_input(value: object) -> str:
        """Isolate the rejected final output for one schema-only repair request."""

        raw_output = value if isinstance(value, str) else str(value)
        return json.dumps({"rejected_final_output": raw_output}, ensure_ascii=False)

    @staticmethod
    def _finalizer_output(value: object) -> object:
        """Extract the FindingBatch object from direct JSON, fenced JSON, or surrounding prose."""

        if not isinstance(value, str):
            return value
        text = value.strip()

        # Strategy 1: direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy 2: scan every object start. JSONDecoder tracks quoted braces correctly and
        # permits a FindingBatch fenced in Markdown or preceded by explanatory prose.
        decoder = json.JSONDecoder()
        first_object: object | None = None
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if not isinstance(candidate, dict):
                continue
            if "schema_version" in candidate and "findings" in candidate:
                return candidate
            if first_object is None:
                first_object = candidate
        if first_object is not None:
            return first_object

        raise AgentOutputParseError(text)

    @staticmethod
    def _is_retryable_provider_error(error: Exception) -> bool:
        if isinstance(
            error, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)
        ):
            return True
        return isinstance(error, APIStatusError) and error.status_code >= 500

    @staticmethod
    def _phase_label(phase: Literal["investigation", "finalizing", "unknown"]) -> str:
        return "Final structured output" if phase == "finalizing" else "Code investigation"

    @classmethod
    def _failure(
        cls,
        phase: Literal["investigation", "finalizing", "unknown"],
        reason_code: str,
        reason: str,
        *,
        retryable: bool,
        provider_status_code: int | None = None,
    ) -> TransientAgentRuntimeError | PermanentAgentOutputError:
        message = f"{cls._phase_label(phase)} failed: {reason}."
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
        cls, error: APIStatusError, phase: Literal["investigation", "finalizing", "unknown"]
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

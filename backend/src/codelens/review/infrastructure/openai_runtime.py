# ruff: noqa: E402

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from typing import Any, Literal, Protocol, cast

import httpx

os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "1")
os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "1")

from agents import (
    Agent,
    FunctionToolResult,
    RawResponsesStreamEvent,
    RunConfig,
    RunContextWrapper,
    RunItemStreamEvent,
    Runner,
    Tool,
    ToolExecutionConfig,
    ToolsToFinalOutputResult,
)
from agents.exceptions import (
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
    UserError,
)
from agents.result import RunResult
from agents.run_config import ToolErrorFormatterArgs
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from codelens.capabilities.domain.models import FrozenAgentExecutionSpec
from codelens.findings.domain.candidates import CandidateFindingBatch
from codelens.review.application.i18n_prompt_loader import I18nPromptLoaderPort
from codelens.review.application.settings import (
    ReviewCompletionSettings,
    ReviewCompletionSettingsService,
)
from codelens.review.application.tool_limits_service import ToolLimitsService
from codelens.review.domain.errors import (
    AgentMaxTurnsExceededError,
    AgentRuntimeError,
    PermanentAgentOutputError,
    TransientAgentRuntimeError,
)
from codelens.review.domain.ports import (
    AgentResponseDiagnostic,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    UnvalidatedAgentOutput,
)
from codelens.review.domain.token_counter import TokenCounterPort
from codelens.review.domain.tool_invocation import classify_tool_result, outcome_metadata
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.domain.tool_results import (
    ToolResultError,
    parse_tool_result,
)
from codelens.review.infrastructure.capability_tools import (
    CapabilityToolAssembler,
    RuntimeToolContext,
    ToolExecutionLimits,
)
from codelens.review.infrastructure.context_checkpoint import (
    CheckpointSummarizerPort,
    CheckpointSummaryRequest,
    CheckpointSummaryResult,
    ContextCheckpointError,
    ContextCheckpointTracker,
    build_context_checkpoint_filter,
    checkpoint_summary_from_text,
)
from codelens.review.infrastructure.evidence_replay import ToolLoopResetSignal
from codelens.review.infrastructure.provider_adapters import ModelProviderAdapterRegistry
from codelens.review.infrastructure.role_execution_strategy import (
    RoleExecutionStrategyRegistry,
)
from codelens.review.infrastructure.token_counter import TiktokenCounterAdapter
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfigPort
from codelens.workspace.domain.models import ReviewSnapshot
from codelens.workspace.infrastructure.git_cli import GitCli

type _AgentFailure = (
    AgentMaxTurnsExceededError | TransientAgentRuntimeError | PermanentAgentOutputError
)
_LOGGER = logging.getLogger(__name__)
_CHECKPOINT_MAX_OUTPUT_TOKENS = 8192
_FORBIDDEN_TOOL_CONTRACT_TERMS = (
    "snapshot_id",
    "hunk_id",
    "content_hash",
    "excerpt_hash",
    "instruction chain",
    "instruction_chain",
    "context plan",
    "context_plan",
    "precedence",
)

def _tool_error_formatter(
    template: str,
    available_tool_names: tuple[str, ...],
) -> Callable[[ToolErrorFormatterArgs[Any]], str | None]:
    """Return localized recovery text for model-invented function tools."""

    available_tools = ", ".join(available_tool_names)

    def format_error(arguments: ToolErrorFormatterArgs[Any]) -> str | None:
        if arguments.kind != "tool_not_found":
            return None
        return template.format(
            tool_name=arguments.tool_name,
            available_tools=available_tools,
        )

    return format_error


def _wrapped_agent_failure(error: BaseException) -> _AgentFailure | None:
    """Recover provider-neutral tool failures wrapped by the Agents SDK."""

    current: BaseException | None = error.__cause__ or error.__context__
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(
            current,
            (AgentMaxTurnsExceededError, TransientAgentRuntimeError, PermanentAgentOutputError),
        ):
            return current
        current = current.__cause__ or current.__context__
    return None


def _cached_input_token_count(usage: object) -> int:
    """Read optional provider cache-hit counter without treating omission as an error."""

    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    return int(cached or 0)


def _response_diagnostic(
    response: Any,
    *,
    phase: Literal["agent", "checkpoint_compaction"] = "agent",
) -> AgentResponseDiagnostic:
    """Normalize bounded usage metadata for main and checkpoint model calls."""

    cached_tokens = _cached_input_token_count(response.usage)
    return AgentResponseDiagnostic(
        response_id=response.response_id,
        request_id=response.request_id,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        output_item_count=len(response.output),
        cached_input_tokens=cached_tokens,
        phase=phase,
    )


class _SdkCheckpointSummarizer:
    """Run checkpoint compaction as one isolated, provider-neutral text call.

    Some MaaS gateways (e.g. Zhipu GLM) ignore ``stream=False`` and always
    return SSE chunks.  When that happens the SDK's
    ``OpenAIChatCompletionsModel.get_response`` raises ``AttributeError``
    because ``_fetch_response`` returns a ``tuple`` instead of a
    ``ChatCompletion``.  For ``OpenAIChatCompletionsModel`` we bypass
    ``Runner.run`` entirely and call the provider directly with
    ``stream=True``, avoiding the slow failure-and-retry cycle inside the
    SDK.  Other model types still go through ``Runner.run``.
    """

    async def summarize(
        self,
        request: CheckpointSummaryRequest,
        agent: Agent[Any],
    ) -> CheckpointSummaryResult:
        checkpoint_settings = replace(
            agent.model_settings,
            max_tokens=min(
                agent.model_settings.max_tokens or _CHECKPOINT_MAX_OUTPUT_TOKENS,
                _CHECKPOINT_MAX_OUTPUT_TOKENS,
            ),
            tool_choice=None,
            parallel_tool_calls=None,
            context_management=None,
        )
        from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

        if isinstance(agent.model, OpenAIChatCompletionsModel):
            return await self._summarize_via_stream(agent, checkpoint_settings, request)

        return await self._summarize_via_runner(agent, checkpoint_settings, request)

    async def _summarize_via_runner(
        self,
        agent: Agent[Any],
        checkpoint_settings: Any,
        request: CheckpointSummaryRequest,
    ) -> CheckpointSummaryResult:
        """Run compaction through the SDK ``Runner.run`` loop."""
        checkpoint_agent: Agent[None] = Agent(
            name=f"{agent.name}:checkpoint-compaction",
            instructions=request.prompt,
            model=agent.model,
            model_settings=checkpoint_settings,
        )
        result = await Runner.run(
            starting_agent=checkpoint_agent,
            input=request.model_input(),
            max_turns=1,
            run_config=RunConfig(trace_include_sensitive_data=False),
        )
        final_output = result.final_output
        if not isinstance(final_output, str) or not final_output.strip():
            raise ValueError("checkpoint model returned empty output")
        return CheckpointSummaryResult(
            summary=checkpoint_summary_from_text(final_output),
            diagnostics=tuple(
                _response_diagnostic(response, phase="checkpoint_compaction")
                for response in result.raw_responses
            ),
        )

    async def _summarize_via_stream(
        self,
        agent: Agent[Any],
        checkpoint_settings: Any,
        request: CheckpointSummaryRequest,
    ) -> CheckpointSummaryResult:
        """Call the provider directly with stream=True and collect chunks.

        This bypasses ``Runner.run`` entirely, so it only works with
        ``OpenAIChatCompletionsModel`` whose internal client is accessible.
        """
        from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

        model = agent.model
        if not isinstance(model, OpenAIChatCompletionsModel):
            raise

        client = model._get_client()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": request.prompt},
            {"role": "user", "content": request.model_input()},
        ]
        create_kwargs: dict[str, Any] = {
            "model": model.model,
            "messages": messages,
            "max_tokens": checkpoint_settings.max_tokens,
            "stream": True,
        }
        if checkpoint_settings.extra_body is not None:
            create_kwargs["extra_body"] = checkpoint_settings.extra_body
        if checkpoint_settings.extra_headers is not None:
            create_kwargs["extra_headers"] = checkpoint_settings.extra_headers
        if checkpoint_settings.extra_query is not None:
            create_kwargs["extra_query"] = checkpoint_settings.extra_query

        stream = await client.chat.completions.create(**create_kwargs)
        content_parts: list[str] = []
        usage: Any = None
        response_id: str | None = None
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    content_parts.append(delta.content)
            if hasattr(chunk, "usage") and chunk.usage is not None:
                usage = chunk.usage
            if hasattr(chunk, "id") and chunk.id:
                response_id = chunk.id

        final_output = "".join(content_parts)
        if not final_output.strip():
            raise ValueError("checkpoint model returned empty output")

        input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        cached_tokens = _cached_input_token_count(usage) if usage else 0

        return CheckpointSummaryResult(
            summary=checkpoint_summary_from_text(final_output),
            diagnostics=(
                AgentResponseDiagnostic(
                    response_id=response_id,
                    request_id=None,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    output_item_count=1,
                    cached_input_tokens=cached_tokens,
                    phase="checkpoint_compaction",
                ),
            ),
        )


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
        git: GitCli,
        prompt_loader: I18nPromptLoaderPort,
        runner: _RunnerPort | None = None,
        completion_settings: ReviewCompletionSettingsService | None = None,
        tool_limits_service: ToolLimitsService | None = None,
        checkpoint_summarizer: CheckpointSummarizerPort | None = None,
        token_counter: TokenCounterPort | None = None,
        strategy_registry: RoleExecutionStrategyRegistry | None = None,
    ) -> None:
        self._config_store = config_store
        self._git = git
        self._prompt_loader = prompt_loader
        self._runner = runner or _PublicSdkRunner()
        self._completion_settings = completion_settings
        self._tool_limits_service = tool_limits_service
        self._checkpoint_summarizer = checkpoint_summarizer or _SdkCheckpointSummarizer()
        self._token_counter = token_counter or TiktokenCounterAdapter()
        self._strategy_registry = strategy_registry or RoleExecutionStrategyRegistry()

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        return await self._invoke(
            execution_spec,
            input_payload,
            snapshot,
            prompt_locale,
            sink=None,
        )

    async def invoke_stream(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
        sink: AgentRuntimeEventSink,
    ) -> UnvalidatedAgentOutput:
        """Emit visible model text and tool evidence while preserving the final checkpoint."""

        return await self._invoke(
            execution_spec,
            input_payload,
            snapshot,
            prompt_locale,
            sink=sink,
        )

    async def _invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
        sink: AgentRuntimeEventSink | None,
    ) -> UnvalidatedAgentOutput:
        self._validate_execution_spec(execution_spec)
        agent = execution_spec.agent
        provider_config = await self._config_store.load()
        if provider_config is None:
            raise PermanentAgentOutputError("Model provider is not configured")
        user_input, repository_instructions, role_context = _split_agent_input(input_payload)
        prompts = self._prompt_loader.get(prompt_locale)
        completion_settings = (
            await self._completion_settings.get()
            if self._completion_settings is not None
            else ReviewCompletionSettings()
        )
        tool_limits = (
            await self._tool_limits_service.get()
            if self._tool_limits_service is not None
            else ToolLimits()
        )
        strategy = self._strategy_registry.resolve(agent.role)
        strategy.validate_output_contract(agent)

        provider_config = replace(
            provider_config,
            max_tokens=execution_spec.execution_limits.max_output_tokens,
            max_agent_turns=execution_spec.execution_limits.max_turns,
            max_tool_calls=execution_spec.execution_limits.max_tool_calls,
        )

        behavior = (
            ModelProviderAdapterRegistry()
            .resolve(provider_config.vendor)
            .request_behavior(provider_config)
        )
        bounded_tool_limits = replace(
            tool_limits,
            max_read_bytes=min(
                tool_limits.max_read_bytes,
                execution_spec.execution_limits.max_tool_result_bytes,
            ),
        )
        role_output = strategy.output_tool_bindings(
            prompts, role_context, snapshot, self._git, bounded_tool_limits
        )
        nudge = strategy.nudge_config(prompts, provider_config)
        checkpoint_tracker = ContextCheckpointTracker()
        loop_reset_signal = ToolLoopResetSignal()
        tool_context = RuntimeToolContext(
            snapshot=snapshot,
            git=self._git,
            tool_descriptions={name: tool.description for name, tool in prompts.tools.items()},
            tool_limits=bounded_tool_limits,
            completion_settings=completion_settings,
            call_limits=ToolExecutionLimits(
                max_tool_calls=execution_spec.execution_limits.max_tool_calls,
                max_identical_tool_results=provider_config.max_identical_tool_results,
                tool_timeout_seconds=provider_config.tool_timeout_seconds,
                tool_loop_warning_template=prompts.tool_loop_warning,
                no_progress_rounds_threshold=nudge.no_progress_rounds_threshold,
                no_progress_nudge_template=nudge.no_progress_nudge_template,
                all_files_reviewed_nudge_template=nudge.all_files_reviewed_nudge_template,
            ),
            role_output_tools=role_output.bindings,
            logical_run_id=_host_run_id(role_context),
            review_feedback=prompts.review_feedback,
            loop_reset_signal=loop_reset_signal,
        )
        model_tools = CapabilityToolAssembler().assemble(execution_spec, tool_context)
        _validate_model_tool_contract(model_tools)
        tool_names = tuple(str(getattr(tool, "name", "")) for tool in model_tools)
        instruction_sections = strategy.instruction_sections(
            prompts,
            repository_instructions,
            agent,
            _skill_instruction_sections(execution_spec),
        )
        run_config = RunConfig(
            trace_include_sensitive_data=False,
            tool_execution=ToolExecutionConfig(max_function_tool_concurrency=None),
            call_model_input_filter=build_context_checkpoint_filter(
                limits=bounded_tool_limits,
                prompt=prompts.checkpoint_compaction,
                tracker=checkpoint_tracker,
                summarizer=self._checkpoint_summarizer,
                loop_reset_signal=loop_reset_signal,
                token_counter=self._token_counter,
            ),
            tool_not_found_behavior="return_error_to_model",
            tool_error_formatter=_tool_error_formatter(
                prompts.tool_not_found,
                tool_names,
            ),
        )
        investigation: object | None = None
        failure: _AgentFailure | None = None
        phase: Literal["investigation", "unknown"] = "investigation"
        max_retries = provider_config.max_retries
        retry_backoff_base = provider_config.retry_backoff_base
        retry_max_delay = provider_config.retry_max_delay
        skills_emitted = sink is None
        prompt_emitted = sink is None
        attempt = 0
        while attempt <= max_retries:
            investigation = None
            if attempt > 0:
                model_tools = CapabilityToolAssembler().assemble(
                    execution_spec, tool_context
                )
            client = AsyncOpenAI(
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            investigation_agent: Agent[None] = Agent(
                name=f"{agent.agent_id}:v{agent.version}",
                instructions="\n\n".join(instruction_sections),
                model=behavior.model_class(
                    model=provider_config.model,
                    openai_client=client,
                ),
                model_settings=behavior.model_settings,
                tools=model_tools,
                tool_use_behavior=_completion_tool_use_behavior(tool_context),
            )
            attempt_failure: _AgentFailure | None = None
            try:
                try:
                    if sink is not None and not skills_emitted:
                        for skill in execution_spec.skills:
                            await sink(
                                AgentRuntimeEvent(
                                    "skill_loaded",
                                    skill.skill_id,
                                    {
                                        "skill_version": str(skill.version),
                                        "content_hash": skill.content_hash,
                                        "activation_reason": skill.activation_reason,
                                    },
                                )
                            )
                        skills_emitted = True
                    if sink is not None and not prompt_emitted:
                        await sink(
                            AgentRuntimeEvent(
                                "prompt",
                                _model_input(
                                    investigation_agent,
                                    user_input,
                                    provider_config.model,
                                    behavior.model_settings,
                                ),
                                {"model_name": provider_config.model},
                            )
                        )
                        prompt_emitted = True
                    investigation = await self._run_observable(
                        investigation_agent,
                        user_input,
                        provider_config.max_agent_turns,
                        run_config,
                        sink,
                        timeout_seconds=execution_spec.execution_limits.timeout_seconds,
                        checkpoint_tracker=checkpoint_tracker,
                    )
                    if sink is not None and investigation is not None:
                        for checkpoint_index, payload in enumerate(
                            checkpoint_tracker.checkpoint_payloads,
                            start=1,
                        ):
                            await sink(
                                AgentRuntimeEvent(
                                    "checkpoint_compaction",
                                    payload,
                                    {
                                        "checkpoint_index": str(checkpoint_index),
                                        "checkpoint_schema_version": (
                                            "codelens_review_checkpoint_v1"
                                        ),
                                    },
                                )
                            )
                        for diagnostic in checkpoint_tracker.diagnostics:
                            response_id = diagnostic.response_id or ""
                            await sink(
                                AgentRuntimeEvent(
                                    "model_started",
                                    "",
                                    {
                                        "response_id": response_id,
                                        "usage_scope": "provider_call",
                                        "model_phase": "checkpoint_compaction",
                                        "event_role": "marker",
                                    },
                                )
                            )
                            await sink(
                                AgentRuntimeEvent(
                                    "model_completed",
                                    "",
                                    {
                                        "response_id": response_id,
                                        "usage_scope": "provider_call",
                                        "model_phase": "checkpoint_compaction",
                                        "model_name": provider_config.model,
                                        "event_role": "marker",
                                        "llm_call_count": "1",
                                        "input_tokens": str(diagnostic.input_tokens),
                                        "cached_input_tokens": str(
                                            diagnostic.cached_input_tokens
                                        ),
                                        "output_tokens": str(diagnostic.output_tokens),
                                        "total_tokens": str(
                                            diagnostic.input_tokens + diagnostic.output_tokens
                                        ),
                                    },
                                )
                            )
                        for response_index, response in enumerate(
                            cast(RunResult, investigation).raw_responses,
                            start=1,
                        ):
                            await sink(
                                AgentRuntimeEvent(
                                    "model_raw_output",
                                    _json_value(response),
                                    {
                                        "response_index": str(response_index),
                                        "parse_failed": "false",
                                    },
                                )
                            )
                    if not tool_context.is_completed:
                        if investigation is not None and not any(
                            getattr(resp, "output", None)
                            for resp in cast(
                                RunResult, investigation
                            ).raw_responses
                        ):
                            attempt_failure = self._failure(
                                phase,
                                "empty_provider_response",
                                "provider returned an empty response",
                                retryable=True,
                            )
                        elif not strategy.requires_completion_nudge or investigation is None:
                            attempt_failure = PermanentAgentOutputError(
                                "Agent run produced no result and did not "
                                "declare completion.",
                                phase="investigation",
                                reason_code="review_completion_not_declared",
                                retryable=False,
                            )
                        else:
                            nudge_tools = CapabilityToolAssembler().assemble(
                                execution_spec, tool_context
                            )
                            nudge_agent: Agent[None] = Agent(
                                name=f"{agent.agent_id}:v{agent.version}",
                                instructions="\n\n".join(instruction_sections),
                                model=behavior.model_class(
                                    model=provider_config.model,
                                    openai_client=client,
                                ),
                                model_settings=behavior.model_settings,
                                tools=nudge_tools,
                                tool_use_behavior=_completion_tool_use_behavior(
                                    tool_context
                                ),
                            )
                            history = cast(
                                RunResult, investigation
                            ).to_input_list()
                            history.append(
                                {"role": "user", "content": prompts.completion_nudge}
                            )
                            nudge_result = await self._run_observable(
                                agent=nudge_agent,
                                input_value=history,
                                max_turns=1,
                                run_config=run_config,
                                sink=sink,
                                timeout_seconds=(
                                    execution_spec.execution_limits.timeout_seconds
                                ),
                                checkpoint_tracker=checkpoint_tracker,
                            )
                            if tool_context.is_completed:
                                investigation = cast(RunResult, nudge_result)
                            elif nudge_result is not None and not any(
                                getattr(resp, "output", None)
                                for resp in cast(
                                    RunResult, nudge_result
                                ).raw_responses
                            ):
                                # Nudge got empty response — retryable, not permanent
                                attempt_failure = self._failure(
                                    phase,
                                    "empty_provider_response",
                                    "provider returned an empty response during nudge",
                                    retryable=True,
                                )
                            else:
                                attempt_failure = PermanentAgentOutputError(
                                    "Agent execution ended without declaring "
                                    "completion after nudge.",
                                    phase="investigation",
                                    reason_code="review_completion_not_declared",
                                    retryable=False,
                                )
                except APIStatusError as provider_error:
                    attempt_failure = self._status_failure(provider_error, phase)
                except APITimeoutError:
                    attempt_failure = self._failure(
                        phase, "provider_timeout", "provider timeout", retryable=True
                    )
                except TimeoutError:
                    attempt_failure = self._failure(
                        phase, "agent_run_timeout", "agent run timed out", retryable=True
                    )
                except APIConnectionError:
                    attempt_failure = self._failure(
                        phase,
                        "provider_connection_error",
                        "provider connection error",
                        retryable=True,
                    )
                except httpx.RemoteProtocolError:
                    attempt_failure = self._failure(
                        phase,
                        "provider_connection_error",
                        "provider connection error",
                        retryable=True,
                    )
                except RateLimitError:
                    attempt_failure = self._failure(
                        phase, "provider_rate_limited", "provider rate limit", retryable=True
                    )
                except ContextCheckpointError:
                    attempt_failure = self._failure(
                        phase,
                        "context_checkpoint_failed",
                        "context checkpoint failed beyond the hard watermark",
                        retryable=False,
                    )
                except InternalServerError:
                    attempt_failure = self._failure(
                        phase, "provider_server_error", "provider server error", retryable=True
                    )
                except APIError:
                    # Bare APIError (e.g. from streaming interruption) is not caught
                    # by the specific subclass handlers above. Treat as transient.
                    attempt_failure = self._failure(
                        phase,
                        "provider_streaming_error",
                        "provider streaming error",
                        retryable=True,
                    )
                except MaxTurnsExceeded:
                    attempt_failure = AgentMaxTurnsExceededError(
                        "Code investigation failed: model used all allowed turns.",
                        phase=phase,
                        reason_code="max_model_turns_exceeded",
                    )
                except (ModelBehaviorError, ModelRefusalError, UserError) as model_error:
                    _LOGGER.warning(
                        "Model produced invalid structured output",
                        extra={"phase": phase, "error": str(model_error)[:500]},
                    )
                    attempt_failure = _wrapped_agent_failure(model_error) or self._failure(
                        phase,
                        "invalid_model_output",
                        "model returned unusable output",
                        retryable=True,
                    )
            except BaseException as exc:
                # Extract partial candidates from tool_context before re-raising
                if (
                    isinstance(exc, AgentRuntimeError)
                    and exc.partial_candidates is None
                    and tool_context.reviewer_output is not None
                ):
                    try:
                        partial_output = tool_context.reviewer_output.final_output()
                        if isinstance(partial_output, CandidateFindingBatch):
                            exc.partial_candidates = partial_output
                    except Exception:
                        # Don't let extraction failure mask the original error
                        pass
                await client.close()
                raise

            if attempt_failure is None:
                break

            await client.close()

            is_retryable = isinstance(attempt_failure, TransientAgentRuntimeError)
            if not is_retryable or attempt >= max_retries:
                failure = attempt_failure
                # Extract partial candidates from tool_context before raising
                if (
                    isinstance(failure, AgentRuntimeError)
                    and failure.partial_candidates is None
                    and "tool_context" in locals()
                    and tool_context.reviewer_output is not None
                ):
                    try:
                        partial_output = tool_context.reviewer_output.final_output()
                        if isinstance(partial_output, CandidateFindingBatch):
                            failure.partial_candidates = partial_output
                    except Exception:
                        # Don't let extraction failure mask the original error
                        pass
                investigation = None
                break

            checkpoint_tracker.reset_context()
            delay = min(retry_backoff_base * (2**attempt), retry_max_delay)
            retry_reason = attempt_failure.reason_code or "unknown"
            _LOGGER.warning(
                "Retrying agent invocation after transient error",
                extra={
                    "phase": phase,
                    "retry_attempt": attempt + 1,
                    "max_retries": max_retries,
                    "delay_seconds": delay,
                    "reason_code": retry_reason,
                },
            )
            if sink is not None:
                await sink(
                    AgentRuntimeEvent(
                        "lifecycle",
                        f"Retrying agent invocation after transient error ({retry_reason})",
                        {
                            "retry_attempt": str(attempt + 1),
                            "max_retries": str(max_retries),
                            "delay_seconds": str(delay),
                            "reason_code": retry_reason,
                        },
                    )
                )
            await asyncio.sleep(delay)

            # 检测是否恢复了非空响应
            recovered = (
                investigation is not None
                and any(
                    getattr(resp, "output", None)
                    for resp in cast(RunResult, investigation).raw_responses
                )
            )

            if recovered:
                # 恢复了非空响应，重置重试计数
                _LOGGER.info(
                    "Resetting retry counter after provider recovery",
                    extra={
                        "phase": phase,
                        "reason_code": retry_reason,
                    },
                )
                attempt = 0
            else:
                attempt += 1

        if failure is not None:
            raise failure from None

        result = cast(RunResult, investigation)
        try:
            canonical_bytes = role_output.serialize_output(tool_context.final_output())
        except ValueError as error:
            await client.close()
            raise PermanentAgentOutputError(
                "Comment tool produced an invalid review output.",
                phase="investigation",
                reason_code="invalid_comment_output",
                retryable=False,
            ) from error

        diagnostics = (
            *tuple(_response_diagnostic(response) for response in result.raw_responses),
            *checkpoint_tracker.diagnostics,
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
            incomplete_review_files=tool_context.incomplete_review_files,
            context_compaction_count=checkpoint_tracker.checkpoint_count,
            context_compacted_result_count=checkpoint_tracker.compacted_result_count,
            context_compaction_original_tokens=checkpoint_tracker.original_tokens,
            context_compaction_compressed_tokens=checkpoint_tracker.compressed_tokens,
            context_compaction_failure_count=checkpoint_tracker.failure_count,
            compaction_replay_registered_count=0,
            compaction_replay_consumed_count=0,
        )
        await client.close()
        return output

    @staticmethod
    def _validate_execution_spec(execution_spec: FrozenAgentExecutionSpec) -> None:
        prompt_hash = hashlib.sha256(
            execution_spec.agent.prompt_template.encode("utf-8")
        ).hexdigest()
        if prompt_hash != execution_spec.prompt_content_hash:
            raise PermanentAgentOutputError(
                "Frozen Reviewer prompt content hash does not match",
                phase="investigation",
                reason_code="prompt_content_hash_mismatch",
                retryable=False,
            )
        for skill in execution_spec.skills:
            content_hash = hashlib.sha256(skill.instruction_text.encode("utf-8")).hexdigest()
            if content_hash != skill.content_hash:
                raise PermanentAgentOutputError(
                    "Frozen Skill content hash does not match",
                    phase="investigation",
                    reason_code="skill_content_hash_mismatch",
                    retryable=False,
                )
        rebuilt = FrozenAgentExecutionSpec.create(
            agent=execution_spec.agent,
            capability_profile=execution_spec.capability_profile,
            skill_policy=execution_spec.skill_policy,
            prompt_content_hash=execution_spec.prompt_content_hash,
            skills=execution_spec.skills,
            execution_limits=execution_spec.execution_limits,
        )
        if rebuilt.fingerprint != execution_spec.fingerprint:
            raise PermanentAgentOutputError(
                "Frozen Agent execution fingerprint does not match",
                phase="investigation",
                reason_code="execution_fingerprint_mismatch",
                retryable=False,
            )

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
        timeout_seconds: float = 3600,
        checkpoint_tracker: ContextCheckpointTracker | None = None,
    ) -> object:
        if sink is None or not hasattr(self._runner, "run_streamed"):
            async with asyncio.timeout(timeout_seconds):
                return await self._runner.run(
                    agent,
                    input_value,
                    max_turns=max_turns,
                    run_config=run_config,
                )
        await sink(
            AgentRuntimeEvent(
                "model_started",
                "",
                {"agent_name": agent.name, "usage_scope": "agent_run", "event_role": "marker"},
            )
        )
        stream = cast(Any, self._runner).run_streamed(
            agent, input_value, max_turns=max_turns, run_config=run_config
        )
        # Track call_id -> tool_name so tool_result entries carry the name of the
        # tool that produced them (the SDK's tool_output item has no name field).
        tool_names: dict[str, str] = {}
        invalid_tool_names: dict[str, str] = {}
        allowed_tool_names = {
            str(getattr(tool, "name", "")) for tool in agent.tools if getattr(tool, "name", "")
        }
        # Accumulate token-level deltas for providers that don't emit *.done.
        output_acc: list[str] = []
        reasoning_acc: list[str] = []
        output_meta: dict[str, str] = {}
        reasoning_meta: dict[str, str] = {}
        async with asyncio.timeout(timeout_seconds):
            async for event in stream.stream_events():
                if isinstance(event, RawResponsesStreamEvent):
                    event_type = str(getattr(event.data, "type", ""))
                    if event_type == "response.output_text.delta":
                        output_acc.append(str(getattr(event.data, "delta", "")))
                        if not output_meta:
                            output_meta = _message_metadata(
                                event.data, "content_index",
                            )
                        continue
                    if event_type == "response.reasoning_summary_text.delta":
                        reasoning_acc.append(str(getattr(event.data, "delta", "")))
                        if not reasoning_meta:
                            reasoning_meta = _message_metadata(
                                event.data, "summary_index",
                            )
                        continue
                    if event_type == "response.output_text.done":
                        text = str(
                            getattr(event.data, "text", "")
                        ) or "".join(output_acc)
                        msg_meta = _message_metadata(
                            event.data, "content_index",
                        )
                        await sink(
                            AgentRuntimeEvent(
                                "model_output_delta", text, msg_meta,
                            )
                        )
                        await sink(
                            AgentRuntimeEvent(
                                "model_output_completed",
                                "",
                                {**msg_meta, "event_role": "marker"},
                            )
                        )
                        output_acc.clear()
                        output_meta = {}
                        continue
                    if event_type == "response.reasoning_summary_text.done":
                        text = (
                            str(getattr(event.data, "summary", ""))
                            or str(getattr(event.data, "text", ""))
                            or "".join(reasoning_acc)
                        )
                        msg_meta = _message_metadata(
                            event.data, "summary_index",
                        )
                        await sink(
                            AgentRuntimeEvent(
                                "model_reasoning_delta", text, msg_meta,
                            )
                        )
                        await sink(
                            AgentRuntimeEvent(
                                "model_reasoning_completed",
                                "",
                                {**msg_meta, "event_role": "marker"},
                            )
                        )
                        reasoning_acc.clear()
                        reasoning_meta = {}
                        continue
                # Flush accumulated text before any non-delta event so the
                # model output appears before tool calls or completion
                # markers.  This covers providers that never emit *.done.
                # Reasoning is flushed before output because reasoning
                # summary deltas always precede output text deltas within a
                # single response, so this preserves arrival order.
                if reasoning_acc:
                    await sink(
                        AgentRuntimeEvent(
                            "model_reasoning_delta",
                            "".join(reasoning_acc),
                            reasoning_meta,
                        )
                    )
                    await sink(
                        AgentRuntimeEvent(
                            "model_reasoning_completed",
                            "",
                            {**reasoning_meta, "event_role": "marker"},
                        )
                    )
                    reasoning_acc.clear()
                    reasoning_meta = {}
                if output_acc:
                    await sink(
                        AgentRuntimeEvent(
                            "model_output_delta",
                            "".join(output_acc),
                            output_meta,
                        )
                    )
                    await sink(
                        AgentRuntimeEvent(
                            "model_output_completed",
                            "",
                            {**output_meta, "event_role": "marker"},
                        )
                    )
                    output_acc.clear()
                    output_meta = {}
                for emitted in _visible_event(event):
                    if emitted.kind == "tool_call":
                        call_id = emitted.metadata.get("tool_call_id")
                        name = emitted.metadata.get("tool_name")
                        if name and name not in allowed_tool_names:
                            if call_id:
                                invalid_tool_names[call_id] = name
                            emitted = AgentRuntimeEvent(
                                "invalid_tool_call",
                                emitted.content,
                                emitted.metadata,
                            )
                            await sink(emitted)
                            continue
                        if call_id and name:
                            tool_names[call_id] = name
                    elif emitted.kind == "tool_result":
                        call_id = emitted.metadata.get("tool_call_id")
                        if call_id and call_id in invalid_tool_names:
                            emitted = AgentRuntimeEvent(
                                "invalid_tool_result",
                                emitted.content,
                                {**emitted.metadata,
                                 "tool_name": invalid_tool_names[call_id]},
                            )
                        elif (
                            call_id
                            and call_id in tool_names
                            and "tool_name" not in emitted.metadata
                        ):
                            emitted.metadata["tool_name"] = tool_names[call_id]
                    elif (
                        emitted.kind == "model_completed"
                        and emitted.metadata.get("usage_scope") == "provider_call"
                        and checkpoint_tracker is not None
                    ):
                        emitted.metadata["context_compaction_count"] = str(
                            checkpoint_tracker.checkpoint_count
                        )
                        emitted.metadata["context_compaction_failure_count"] = str(
                            checkpoint_tracker.failure_count
                        )
                        emitted.metadata["context_compacted_result_count"] = str(
                            checkpoint_tracker.compacted_result_count
                        )
                        emitted.metadata["context_compaction_original_tokens"] = str(
                            checkpoint_tracker.original_tokens
                        )
                        emitted.metadata["context_compaction_compressed_tokens"] = str(
                            checkpoint_tracker.compressed_tokens
                        )
                    await sink(emitted)
        # Flush remaining accumulated text for providers that never emit
        # *.done and whose response ended without a non-delta event.
        # Reasoning is flushed before output to preserve arrival order.
        if reasoning_acc:
            await sink(
                AgentRuntimeEvent(
                    "model_reasoning_delta",
                    "".join(reasoning_acc),
                    reasoning_meta,
                )
            )
            await sink(
                AgentRuntimeEvent(
                    "model_reasoning_completed",
                    "",
                    {**reasoning_meta, "event_role": "marker"},
                )
            )
        if output_acc:
            await sink(
                AgentRuntimeEvent(
                    "model_output_delta", "".join(output_acc), output_meta,
                )
            )
            await sink(
                AgentRuntimeEvent(
                    "model_output_completed",
                    "",
                    {**output_meta, "event_role": "marker"},
                )
            )
        await sink(
            AgentRuntimeEvent(
                "model_completed",
                "",
                {"agent_name": agent.name, "usage_scope": "agent_run", "event_role": "marker"},
            )
        )
        return stream


def _visible_event(event: object) -> list[AgentRuntimeEvent]:
    """Map response boundaries and tool events to console records.

    Token-level deltas and ``*.done`` events are handled directly in the
    stream loop (accumulator + flush), not here.  This function covers
    ``response.created``, ``response.completed``, and ``RunItemStreamEvent``
    (tool calls and tool results).  Marker events are tagged with
    ``event_role: "marker"`` so display layers can filter them uniformly.
    """

    if isinstance(event, RawResponsesStreamEvent):
        payload = event.data
        event_type = getattr(payload, "type", "")
        if event_type == "response.created":
            response = getattr(payload, "response", None)
            return [AgentRuntimeEvent(
                "model_started",
                "",
                {
                    "response_id": str(getattr(response, "id", "")),
                    "usage_scope": "provider_call",
                    "event_role": "marker",
                },
            )]
        if event_type == "response.completed":
            response = getattr(payload, "response", None)
            usage = getattr(response, "usage", None)
            metadata: dict[str, str] = {
                "response_id": str(getattr(response, "id", "")),
                "usage_scope": "provider_call",
                "model_name": str(getattr(response, "model", "")),
                "event_role": "marker",
            }
            if usage is not None:
                cached_tokens = _cached_input_token_count(usage)
                metadata.update(
                    {
                        "llm_call_count": "1",
                        "input_tokens": str(getattr(usage, "input_tokens", "")),
                        "cached_input_tokens": str(cached_tokens),
                        "output_tokens": str(getattr(usage, "output_tokens", "")),
                        "total_tokens": str(getattr(usage, "total_tokens", "")),
                    }
                )
            return [AgentRuntimeEvent("model_completed", "", metadata)]
        # Token-level deltas and *.done events are handled directly in the
        # stream loop (accumulator + flush), not here.
        return []
    if isinstance(event, RunItemStreamEvent):
        if event.name == "tool_called":
            return [AgentRuntimeEvent(
                "tool_call",
                _json_value(getattr(event.item, "raw_item", event.item)),
                _tool_metadata(event.item, include_name=True),
            )]
        if event.name == "tool_output":
            output = getattr(event.item, "output", event.item)
            content = output if isinstance(output, str) else _json_value(output)
            metadata = {
                **_tool_metadata(event.item),
                **outcome_metadata(classify_tool_result(content)),
            }
            try:
                result = parse_tool_result(content)
            except ToolResultError:
                metadata["non_json_tool_result"] = "true"
            else:
                metadata["tool_result_status"] = result.status.value
            return [AgentRuntimeEvent(
                "tool_result",
                content,
                metadata,
            )]
    return []


def _message_metadata(payload: object, index_name: str) -> dict[str, str]:
    """Return a stable per-content-part ID shared by stream deltas and completion events."""

    item_id = str(getattr(payload, "item_id", ""))
    index = str(getattr(payload, index_name, ""))
    return {"message_id": f"{item_id}:{index}"}


def _json_value(value: object) -> str:
    return json.dumps(_json_compatible(value), ensure_ascii=False, sort_keys=True, default=str)


def _json_compatible(value: object) -> object:
    """Convert SDK and dataclass values without dropping provider response fields."""

    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_compatible(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _split_agent_input(input_payload: bytes) -> tuple[str, str, dict[str, object] | None]:
    """Split the internal context envelope into user scope and trusted instructions."""

    try:
        decoded = input_payload.decode("utf-8", errors="strict")
        envelope = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PermanentAgentOutputError("Agent input is not valid JSON UTF-8") from None

    if not isinstance(envelope, dict) or set(envelope) not in (
        {"review_files", "repository_instructions"},
        {"review_files", "repository_instructions", "role_context"},
    ):
        raise PermanentAgentOutputError("Agent input envelope has an invalid shape")
    review_files = envelope["review_files"]
    repository_instructions = envelope["repository_instructions"]
    role_context = envelope.get("role_context")
    if not isinstance(review_files, list) or not all(
        isinstance(item, dict) for item in review_files
    ):
        raise PermanentAgentOutputError("Agent review_files input has an invalid shape")
    if not isinstance(repository_instructions, list) or not all(
        isinstance(item, dict) for item in repository_instructions
    ):
        raise PermanentAgentOutputError("Agent repository_instructions input has an invalid shape")
    if role_context is not None and not isinstance(role_context, dict):
        raise PermanentAgentOutputError("Agent role_context input has an invalid shape")

    model_role_context = (
        {key: value for key, value in role_context.items() if not key.startswith("_host_")}
        if role_context is not None
        else None
    )
    return (
        json.dumps(
            {
                "review_file_count": len(review_files),
                "review_files": review_files,
                **({"role_context": model_role_context} if model_role_context else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        json.dumps(
            {"repository_instructions": repository_instructions},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        cast(dict[str, object] | None, role_context),
    )


def _host_run_id(role_context: dict[str, object] | None) -> str | None:
    if role_context is None or "_host_run_id" not in role_context:
        return None
    value = role_context["_host_run_id"]
    if not isinstance(value, str) or not value.startswith("run_") or len(value) != 68:
        raise PermanentAgentOutputError("Agent host run identity is invalid")
    return value


def _skill_instruction_sections(
    execution_spec: FrozenAgentExecutionSpec,
) -> tuple[str, ...]:
    """Render activated skill instructions as untrusted, permission-isolated sections."""

    return tuple(
        "\n".join(
            (
                "# Activated Review Skill (Untrusted, No Additional Permissions)",
                json.dumps(
                    {
                        "skill_id": skill.skill_id,
                        "version": skill.version,
                        "content_hash": skill.content_hash,
                        "activation_reason": skill.activation_reason,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "<skill-instructions>",
                skill.instruction_text,
                "</skill-instructions>",
            )
        )
        for skill in execution_spec.skills
    )


def _model_input(
    agent: Agent[None],
    user_input: str,
    model_name: str,
    model_settings: object,
) -> str:
    """Serialize the complete model-visible instructions, input, tool schemas, and settings."""

    tools = tuple(
        {
            "name": str(getattr(tool, "name", "")),
            "description": str(getattr(tool, "description", "")),
            "parameters": _json_compatible(getattr(tool, "params_json_schema", {})),
            "strict_json_schema": bool(getattr(tool, "strict_json_schema", False)),
        }
        for tool in agent.tools
    )
    return json.dumps(
        {
            "model": model_name,
            "model_settings": _json_compatible(model_settings),
            "system_instructions": str(agent.instructions),
            "tools": tools,
            "user_input": user_input,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _validate_model_tool_contract(tools: list[Tool]) -> None:
    """Fail before provider I/O if a model-visible tool exposes internal concepts."""

    payload = json.dumps(
        tuple(
            {
                "name": str(getattr(tool, "name", "")),
                "description": str(getattr(tool, "description", "")),
                "parameters": _json_compatible(getattr(tool, "params_json_schema", {})),
            }
            for tool in tools
        ),
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    leaked = next((term for term in _FORBIDDEN_TOOL_CONTRACT_TERMS if term in payload), None)
    if leaked is not None:
        raise PermanentAgentOutputError("Model tool contract exposes internal Review metadata")


def _completion_tool_use_behavior(
    tool_context: RuntimeToolContext,
) -> Callable[
    [RunContextWrapper[None], list[FunctionToolResult]],
    ToolsToFinalOutputResult,
]:
    """Stop the SDK loop only after the host accepted a task_done submission."""

    def decide(
        _context: RunContextWrapper[None],
        _tool_results: list[FunctionToolResult],
    ) -> ToolsToFinalOutputResult:
        if tool_context.is_completed:
            return ToolsToFinalOutputResult(is_final_output=True, final_output="")
        return ToolsToFinalOutputResult(is_final_output=False)

    return decide


def _tool_metadata(value: object, *, include_name: bool = False) -> dict[str, str]:
    """Extract stable tool identity without exposing SDK item types past this adapter.

    The SDK's ``RunItem`` subclasses expose ``call_id``/``tool_name`` as *properties*
    that correctly handle dict-backed ``raw_item`` instances (e.g.
    ``FunctionCallOutput`` which subclasses ``dict``).  ``getattr`` on a dict does
    not access keys, so we must try the item's own property first, then fall back
    to the raw item for non-SDK callers.
    """

    metadata: dict[str, str] = {}
    call_id = _extract_identity(value, "call_id")
    if call_id:
        metadata["tool_call_id"] = call_id
    if include_name:
        name = _extract_identity(value, "tool_name") or _extract_identity(value, "name")
        if name:
            metadata["tool_name"] = name
    return metadata


def _extract_identity(value: object, attr: str) -> str | None:
    """Return a non-empty string for *attr* from a RunItem or its raw_item."""

    prop = getattr(value, attr, None)
    if isinstance(prop, str) and prop:
        return prop
    raw_item = getattr(value, "raw_item", None)
    if isinstance(raw_item, dict):
        candidate = raw_item.get(attr)
    else:
        candidate = getattr(raw_item, attr, None)
    return candidate if isinstance(candidate, str) and candidate else None

# ruff: noqa: E402

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass, replace
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
    ToolsToFinalOutputResult,
)
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

from codelens.capabilities.domain.models import FrozenAgentExecutionSpec
from codelens.findings.application.validate_candidates import CandidateBatchCodec
from codelens.findings.domain.candidates import CandidateFindingBatch
from codelens.findings.domain.models import FindingSeverity
from codelens.findings.domain.resolution import (
    FindingCluster,
    ResolutionDecision,
    VerificationDecision,
)
from codelens.findings.infrastructure.resolver_output import ResolverOutputCodec
from codelens.findings.infrastructure.verifier_output import VerifierOutputCodec
from codelens.review.application.i18n_prompt_loader import I18nPromptLoaderPort
from codelens.review.application.settings import (
    ReviewCompletionSettings,
    ReviewCompletionSettingsService,
)
from codelens.review.application.tool_limits_service import ToolLimitsService
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
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.capability_tools import (
    CapabilityToolAssembler,
    RoleOutputToolBinding,
    RuntimeToolContext,
    ToolExecutionLimits,
)
from codelens.review.infrastructure.planner_output import PlannerOutputCodec
from codelens.review.infrastructure.planning_tools import ReviewPlanSubmissionCollector
from codelens.review.infrastructure.provider_adapters import ModelProviderAdapterRegistry
from codelens.review.infrastructure.resolution_tools import ResolutionSubmissionCollector
from codelens.review.infrastructure.verification_tools import VerificationSubmissionCollector
from codelens.reviewer_catalog.domain.models import AgentRole
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfigPort
from codelens.workspace.domain.models import ReviewSnapshot
from codelens.workspace.infrastructure.git_cli import GitCli

type _AgentFailure = (
    AgentMaxTurnsExceededError | TransientAgentRuntimeError | PermanentAgentOutputError
)
_LOGGER = logging.getLogger(__name__)
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
        completion_settings: ReviewCompletionSettingsService | None = None,
        tool_limits_service: ToolLimitsService | None = None,
    ) -> None:
        self._config_store = config_store
        self._output_codec = output_codec
        self._git = git
        self._prompt_loader = prompt_loader
        self._runner = runner or _PublicSdkRunner()
        self._completion_settings = completion_settings
        self._tool_limits_service = tool_limits_service

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
        is_reviewer = agent.role is AgentRole.REVIEWER
        if is_reviewer and agent.output_contract_version not in {
            self._output_codec.schema_version,
            "2",
        }:
            raise PermanentAgentOutputError("Agent output contract is unsupported")

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
        role_output_tools: tuple[RoleOutputToolBinding, ...] = ()
        planner_codec: PlannerOutputCodec | None = None
        resolver_codec: ResolverOutputCodec | None = None
        verifier_codec: VerifierOutputCodec | None = None
        if agent.role is AgentRole.PLANNER:
            planner_codec = _planner_codec(role_context)
            planner_collector = ReviewPlanSubmissionCollector(planner_codec)
            planner_description = prompts.tools["submit_review_plan"].description
            role_output_tools = (planner_collector.binding(planner_description),)
        elif agent.role is AgentRole.RESOLVER:
            resolver_codec = _resolver_codec(role_context)
            resolver_collector = ResolutionSubmissionCollector(resolver_codec)
            resolver_description = prompts.tools["submit_resolution"].description
            role_output_tools = (resolver_collector.binding(resolver_description),)
        elif agent.role is AgentRole.VERIFIER:
            verifier_codec = _verifier_codec(role_context)
            verifier_collector = VerificationSubmissionCollector(verifier_codec)
            verifier_description = prompts.tools["submit_verification"].description
            role_output_tools = (verifier_collector.binding(verifier_description),)
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
            ),
            role_output_tools=role_output_tools,
            logical_run_id=_host_run_id(role_context),
        )
        model_tools = CapabilityToolAssembler().assemble(execution_spec, tool_context)
        _validate_model_tool_contract(model_tools)
        client = AsyncOpenAI(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
            http_client=httpx.AsyncClient(trust_env=False),
        )
        instruction_sections = [prompts.review_policy, repository_instructions]
        if is_reviewer:
            instruction_sections.append(prompts.review_workflow)
        instruction_sections.extend(
            (
                f"# Agent Policy\n{agent.prompt_template}",
                *self._skill_instruction_sections(execution_spec),
            )
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
        run_config = RunConfig(trace_include_sensitive_data=False)
        investigation: object | None = None
        failure: _AgentFailure | None = None
        phase: Literal["investigation", "unknown"] = "investigation"
        try:
            try:
                if sink is not None:
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
                investigation = await self._run_observable(
                    investigation_agent,
                    user_input,
                    provider_config.max_agent_turns,
                    run_config,
                    sink,
                    timeout_seconds=execution_spec.execution_limits.timeout_seconds,
                )
                if sink is not None and investigation is not None:
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
        if not tool_context.is_completed:
            await client.close()
            raise PermanentAgentOutputError(
                "Agent execution ended without an accepted output submission.",
                phase="investigation",
                reason_code="review_completion_not_declared",
                retryable=False,
            ) from None
        try:
            if planner_codec is not None:
                final_output = tool_context.final_output()
                from codelens.review.application.planning import PlannerSelection

                if not isinstance(final_output, PlannerSelection):
                    raise ValueError("Planner output state has the wrong value")
                canonical_bytes = planner_codec.canonical_bytes(final_output)
            elif resolver_codec is not None:
                final_output = tool_context.final_output()
                if not isinstance(final_output, tuple) or not all(
                    isinstance(item, ResolutionDecision) for item in final_output
                ):
                    raise ValueError("Resolver output state has the wrong value")
                canonical_bytes = resolver_codec.canonical_bytes(final_output)
            elif verifier_codec is not None:
                final_output = tool_context.final_output()
                if not isinstance(final_output, tuple) or not all(
                    isinstance(item, VerificationDecision) for item in final_output
                ):
                    raise ValueError("Verifier output state has the wrong value")
                canonical_bytes = verifier_codec.canonical_bytes(final_output)
            elif agent.output_contract_version == "2":
                final_output = tool_context.final_output()
                if not isinstance(final_output, CandidateFindingBatch):
                    raise ValueError("Comment v2 output state has the wrong value")
                canonical_bytes = CandidateBatchCodec().encode(final_output)
            else:
                canonical_bytes = self._output_codec.encode(tool_context.final_output())
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
            incomplete_review_files=tool_context.incomplete_review_files,
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

    @staticmethod
    def _skill_instruction_sections(
        execution_spec: FrozenAgentExecutionSpec,
    ) -> tuple[str, ...]:
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
        timeout_seconds: float = 1800,
    ) -> object:
        if sink is None or not hasattr(self._runner, "run_streamed"):
            async with asyncio.timeout(timeout_seconds):
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
            return AgentRuntimeEvent(
                "tool_call",
                _json_value(getattr(event.item, "raw_item", event.item)),
                _tool_metadata(event.item, include_name=True),
            )
        if event.name == "tool_output":
            return AgentRuntimeEvent(
                "tool_result",
                _json_value(getattr(event.item, "output", event.item)),
                _tool_metadata(event.item),
            )
    return None


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
        raise PermanentAgentOutputError(
            "Agent repository_instructions input has an invalid shape"
        )
    if role_context is not None and not isinstance(role_context, dict):
        raise PermanentAgentOutputError("Agent role_context input has an invalid shape")

    model_role_context = (
        {
            key: value
            for key, value in role_context.items()
            if not key.startswith("_host_")
        }
        if role_context is not None
        else None
    )
    return (
        json.dumps(
            {
                "review_files": review_files,
                **(
                    {"role_context": model_role_context}
                    if model_role_context
                    else {}
                ),
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


def _planner_codec(role_context: dict[str, object] | None) -> PlannerOutputCodec:
    """Build the Planner validator only from bounded frozen input metadata."""

    required = {
        "eligible_reviewer_references",
        "unavailable_reviewer_references",
        "target_paths",
        "allowed_reason_codes",
    }
    allowed = required | {"budget_limits", "change_risk_summary", "reviewer_catalog"}
    if role_context is None or not required.issubset(role_context) or not set(
        role_context
    ).issubset(allowed):
        raise PermanentAgentOutputError("Planner role context has an invalid shape")

    def string_tuple(name: str) -> tuple[str, ...]:
        value = role_context[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PermanentAgentOutputError("Planner role context has an invalid value")
        return tuple(value)

    return PlannerOutputCodec(
        eligible_reviewer_references=string_tuple("eligible_reviewer_references"),
        unavailable_reviewer_references=string_tuple(
            "unavailable_reviewer_references"
        ),
        target_paths=string_tuple("target_paths"),
        allowed_reason_codes=frozenset(string_tuple("allowed_reason_codes")),
    )


def _host_run_id(role_context: dict[str, object] | None) -> str | None:
    if role_context is None or "_host_run_id" not in role_context:
        return None
    value = role_context["_host_run_id"]
    if not isinstance(value, str) or not value.startswith("run_") or len(value) != 68:
        raise PermanentAgentOutputError("Agent host run identity is invalid")
    return value


@dataclass(frozen=True)
class _ResolverCandidate:
    candidate_id: str
    severity: FindingSeverity


def _resolver_codec(role_context: dict[str, object] | None) -> ResolverOutputCodec:
    """Rebuild Resolver constraints from the bounded, execution-order-free projection."""

    context = role_context.get("resolution_context") if role_context is not None else None
    if not isinstance(context, dict) or set(context) != {
        "candidates",
        "clusters",
        "schema_version",
    }:
        raise PermanentAgentOutputError("Resolver role context has an invalid shape")
    raw_clusters = context["clusters"]
    raw_candidates = context["candidates"]
    if context["schema_version"] != "1" or not isinstance(
        raw_clusters, list
    ) or not isinstance(raw_candidates, list):
        raise PermanentAgentOutputError("Resolver role context has an invalid value")
    try:
        clusters = tuple(
            FindingCluster(
                cluster_id=item["cluster_id"],
                candidate_ids=tuple(item["candidate_ids"]),
            )
            for item in raw_clusters
            if isinstance(item, dict)
        )
        candidates = tuple(
            _ResolverCandidate(
                candidate_id=item["candidate_id"],
                severity=FindingSeverity(item["severity"]),
            )
            for item in raw_candidates
            if isinstance(item, dict)
        )
        if len(clusters) != len(raw_clusters) or len(candidates) != len(raw_candidates):
            raise ValueError("Resolver projection contains non-object values")
        return ResolverOutputCodec(clusters, candidates)
    except (KeyError, TypeError, ValueError) as error:
        raise PermanentAgentOutputError(
            "Resolver role context has an invalid value"
        ) from error


def _verifier_codec(role_context: dict[str, object] | None) -> VerifierOutputCodec:
    context = (
        role_context.get("verification_context") if role_context is not None else None
    )
    if not isinstance(context, dict) or set(context) != {
        "cluster_ids",
        "decisions",
        "schema_version",
    }:
        raise PermanentAgentOutputError("Verifier role context has an invalid shape")
    cluster_ids = context["cluster_ids"]
    if (
        context["schema_version"] != "1"
        or not isinstance(cluster_ids, list)
        or not all(isinstance(item, str) for item in cluster_ids)
        or not isinstance(context["decisions"], list)
    ):
        raise PermanentAgentOutputError("Verifier role context has an invalid value")
    try:
        return VerifierOutputCodec(tuple(cluster_ids))
    except ValueError as error:
        raise PermanentAgentOutputError(
            "Verifier role context has an invalid value"
        ) from error


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
    """Extract stable tool identity without exposing SDK item types past this adapter."""

    raw_item = getattr(value, "raw_item", None)
    metadata: dict[str, str] = {}
    call_id = getattr(raw_item, "call_id", None)
    if isinstance(call_id, str) and call_id:
        metadata["tool_call_id"] = call_id
    if include_name:
        name = getattr(raw_item, "name", None)
        if isinstance(name, str) and name:
            metadata["tool_name"] = name
    return metadata

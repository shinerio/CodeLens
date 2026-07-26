import json
import traceback
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from agents import Agent, RunConfig
from agents.exceptions import ModelBehaviorError
from openai import APIConnectionError, InternalServerError, RateLimitError

from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.findings.infrastructure.model_output import FindingBatchSchema
from codelens.review.domain.errors import (
    PermanentAgentOutputError,
    TransientAgentRuntimeError,
)
from codelens.review.infrastructure.i18n_prompt_loader import I18nPromptLoader
from codelens.review.infrastructure.openai_runtime import OpenAIAgentRuntime
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfig
from codelens.reviewer_catalog.infrastructure.builtin_agents import correctness_agent
from codelens.workspace.domain.models import (
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotManifest,
    TaskWorktree,
)
from codelens.workspace.infrastructure.git_cli import GitCli


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

    def to_input_list(self) -> str:
        return "controlled tool history"


class FakeRunner:
    def __init__(self, result: FakeResult | Exception) -> None:
        self.result = result
        self.starting_agent: Agent[None] | None = None
        self.input_payload: str | None = None
        self.max_turns: int | None = None
        self.run_config: RunConfig | None = None
        self.calls: list[tuple[Agent[None], object, int]] = []

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
        self.calls.append((starting_agent, input, max_turns))
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
        api_type="responses",
    )


def _prompt_loader() -> I18nPromptLoader:
    return I18nPromptLoader.load(Path(__file__).parents[4] / "prompts")


def _agent() -> AgentVersion:
    return AgentVersion(
        agent_id="correctness",
        version=1,
        prompt_template="PROMPT_SECRET: inspect the bounded Snapshot input.",
        model_profile_id="balanced",
        output_contract_version="1",
        timeout_seconds=30.0,
        max_turns=3,
        confidence_floor=0.7,
        failure_policy="fail_task",
        content_hash="a" * 64,
    )


def _snapshot() -> ReviewSnapshot:
    return ReviewSnapshot(
        snapshot_id="snapshot-1",
        worktree=TaskWorktree("worktree-1", "review-1", "a" * 64, Path("/tmp"), "b" * 40, "c" * 64),
        target=ReviewTarget("d" * 40, "b" * 40, None),
        fingerprint=RepositoryFingerprint("b" * 40, "e" * 64, "f" * 64),
        manifest=SnapshotManifest((), (), (), entries=()),
        change_index=ChangeIndex(()),
    )


async def test_successful_provider_responses_are_not_marked_as_parse_failures() -> None:
    runner = FakeRunner(
        FakeResult(
            final_output=FindingBatchSchema(schema_version="1", findings=()),
            raw_responses=(FakeResponse("resp_1", "req_1", FakeUsage(1, 1), ()),),
        )
    )
    events = []

    async def record_event(event: object) -> None:
        events.append(event)

    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(_provider_config()),
        output_codec=AgentOutputCodec("1"),
        git=GitCli(),
        prompt_loader=_prompt_loader(),
        runner=runner,
    )

    await runtime.invoke_stream(_agent(), b'{"review_files":[]}', _snapshot(), "en", record_event)

    raw_events = [event for event in events if event.kind == "model_raw_output"]
    assert len(raw_events) == 1
    assert raw_events[0].metadata == {"parse_failed": "false", "response_index": "1"}


def test_prompt_loader_validates_the_complete_model_visible_tool_set_for_each_locale() -> None:
    loader = _prompt_loader()
    expected = {
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "comment",
        "task_done",
    }

    assert set(loader.get("en").tools) == expected
    assert set(loader.get("zh-CN").tools) == expected


async def test_ignores_model_final_text_without_a_comment_tool_call() -> None:
    finding = {
        "reviewer_id": "correctness",
        "category": "correctness",
        "title": "Missing bounds check",
        "severity": "high",
        "disposition": "blocking",
        "confidence": 0.9,
        "primary_location": {
            "path": "src/example.py",
            "start_line": 10,
            "end_line": 10,
            "side": "new",
            "excerpt_hash": "a" * 64,
        },
        "changed_hunk_id": "hunk-1",
        "change_origin": "introduced",
        "evidence": [{"kind": "excerpt", "description": "Input is used unchecked."}],
        "impact": "An invalid input can reach the protected operation.",
        "explanation": "The newly added path has no range validation.",
        "recommendation": "Validate the input before using it.",
    }
    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(_provider_config()),
        output_codec=AgentOutputCodec("1"),
        git=GitCli(),
        prompt_loader=_prompt_loader(),
        runner=FakeRunner(FakeResult({"schema_version": "1", "findings": [finding]}, ())),
    )

    output = await runtime.invoke(_agent(), b"bounded input", _snapshot(), "en")

    payload = json.loads(output.canonical_bytes)
    assert payload["findings"] == []


async def test_streaming_investigation_closes_client_after_a_non_streaming_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.is_closed = False
            self.close_count = 0

        async def close(self) -> None:
            self.is_closed = True
            self.close_count += 1

    class ClientAwareRunner(FakeRunner):
        async def run(
            self,
            starting_agent: Agent[None],
            input: str,
            *,
            max_turns: int,
            run_config: RunConfig,
        ) -> FakeResult:
            client = starting_agent.model._client
            assert client.is_closed is False
            self.calls.append((starting_agent, input, max_turns))
            return FakeResult(FindingBatchSchema(schema_version="1", findings=()), ())

    client = RecordingClient()
    monkeypatch.setattr(
        "codelens.review.infrastructure.openai_runtime.AsyncOpenAI",
        lambda **_: client,
    )
    events = []

    async def record_event(event: object) -> None:
        events.append(event)

    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(_provider_config()),
        output_codec=AgentOutputCodec("1"),
        git=GitCli(),
        prompt_loader=_prompt_loader(),
        runner=ClientAwareRunner(FakeResult({}, ())),
    )

    output = await runtime.invoke_stream(
        _agent(), b"bounded input", _snapshot(), "en", record_event
    )

    assert output.canonical_bytes == b'{"findings":[],"schema_version":"1"}'
    assert client.close_count == 1
    assert len(events) == 1
    assert events[0].kind == "prompt"


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
        git=GitCli(),
        prompt_loader=_prompt_loader(),
        runner=FakeRunner(failure),
    )

    with pytest.raises(TransientAgentRuntimeError) as captured:
        await runtime.invoke(_agent(), b"bounded input", _snapshot(), "en")

    assert "rate limited" not in str(captured.value)
    assert "server failed" not in str(captured.value)
    assert captured.value.reason_code in {
        "provider_connection_error",
        "provider_rate_limited",
        "provider_server_error",
    }
    assert captured.value.phase == "investigation"
    assert captured.value.retryable is True


@pytest.mark.parametrize("result", [ModelBehaviorError("FULL_PROVIDER_PAYLOAD_SECRET")])
async def test_maps_invalid_investigation_to_a_permanent_failure(result: Exception) -> None:
    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(_provider_config()),
        output_codec=AgentOutputCodec("1"),
        git=GitCli(),
        prompt_loader=_prompt_loader(),
        runner=FakeRunner(result),
    )

    with pytest.raises(PermanentAgentOutputError) as captured:
        await runtime.invoke(_agent(), b"bounded input", _snapshot(), "en")

    assert "FULL_PROVIDER_PAYLOAD_SECRET" not in str(captured.value)
    formatted = "".join(traceback.format_exception(captured.value))
    assert "FULL_PROVIDER_PAYLOAD_SECRET" not in formatted
    assert captured.value.__context__ is None


async def test_missing_provider_configuration_fails_only_when_invoked() -> None:
    runtime = OpenAIAgentRuntime(
        config_store=StaticProviderConfigStore(),
        output_codec=AgentOutputCodec("1"),
        git=GitCli(),
        prompt_loader=_prompt_loader(),
        runner=FakeRunner(FakeResult({}, ())),
    )

    with pytest.raises(PermanentAgentOutputError, match="not configured"):
        await runtime.invoke(_agent(), b"bounded input", _snapshot(), "en")


def test_builtin_correctness_agent_is_immutable_and_content_addressed() -> None:
    first = correctness_agent()
    second = correctness_agent()

    assert first == second
    assert first.agent_id == "correctness"
    assert first.output_contract_version == "1"
    assert first.prompt_template == "Prompt template is loaded from the prompt catalog at runtime."
    assert len(first.content_hash) == 64

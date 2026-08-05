import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents import RunConfig, Usage
from agents.tool_context import ToolContext

from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    FrozenAgentExecutionSpec,
    SkillPolicyReference,
    ToolContractReference,
)
from codelens.capabilities.infrastructure.builtin_profiles import builtin_capability_profiles
from codelens.findings.domain.candidates import CandidateFindingBatch
from codelens.review.domain.errors import PermanentAgentOutputError
from codelens.review.infrastructure.capability_tools import (
    CapabilityToolAssembler,
    RuntimeToolContext,
    ToolExecutionLimits,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
from codelens.workspace.domain.models import (
    ChangedHunk,
    ChangeIndex,
    RepositoryFingerprint,
    ReviewFileChange,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotEntry,
    SnapshotManifest,
    TaskWorktree,
)


def _resolved_spec(agent_reference: str) -> FrozenAgentExecutionSpec:
    agent = builtin_agent_catalog()[agent_reference]
    profile = builtin_capability_profiles()[agent.capability_profile_ref]
    return FrozenAgentExecutionSpec.create(
        agent=agent,
        capability_profile=profile,
        skill_policy=SkillPolicyReference("none", 1),
        prompt_content_hash="a" * 64,
        skills=(),
        execution_limits=AgentExecutionLimits.legacy_default(),
    )


@pytest.fixture
def review_snapshot(tmp_path: Path) -> ReviewSnapshot:
    source = b"value = 2\n"
    path = "src/value.py"
    source_path = tmp_path / path
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(source)
    source_hash = hashlib.sha256(source).hexdigest()
    return ReviewSnapshot(
        "snapshot-1",
        TaskWorktree(
            "worktree-1",
            "review-1",
            "a" * 64,
            tmp_path,
            "b" * 40,
            "c" * 64,
        ),
        ReviewTarget("a" * 40, "b" * 40, None),
        RepositoryFingerprint("b" * 40, "d" * 64, "e" * 64),
        SnapshotManifest(
            (path,),
            (),
            (),
            entries=(SnapshotEntry(path, "file", 0o644, len(source), source_hash, None, "target"),),
        ),
        ChangeIndex(
            (ChangedHunk("hunk-1", path, 1, 1, "new", source_hash),),
            (ReviewFileChange(path, "modified"),),
        ),
    )


@pytest.fixture
def runtime_context(review_snapshot: ReviewSnapshot) -> RuntimeToolContext:
    return RuntimeToolContext.for_test(
        snapshot=review_snapshot,
        call_limits=ToolExecutionLimits.testing(),
    )


def _tool_names(
    agent_reference: str,
    runtime_context: RuntimeToolContext,
) -> tuple[str, ...]:
    tools = CapabilityToolAssembler().assemble(
        _resolved_spec(agent_reference),
        runtime_context,
    )
    return tuple(tool.name for tool in tools)


def test_legacy_reviewer_assembles_comment_v1_only(
    runtime_context: RuntimeToolContext,
) -> None:
    assert _tool_names("correctness:v1", runtime_context) == (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "comment",
        "review_file_done",
        "task_done",
    )
    assert runtime_context.collector_contract_version == "1"
    assert runtime_context.is_completed is False
    assert runtime_context.final_output() == {"schema_version": "1", "findings": ()}


def test_comment_v2_reviewer_preserves_order_and_selects_v2_collector(
    runtime_context: RuntimeToolContext,
) -> None:
    assert _tool_names("security:v1", runtime_context) == (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "comment",
        "review_file_done",
        "task_done",
    )
    assert runtime_context.collector_contract_version == "2"
    assert runtime_context.is_completed is False
    output = runtime_context.final_output()
    assert isinstance(output, CandidateFindingBatch)
    assert output.schema_version == "2"
    assert output.candidates == ()


async def test_v2_context_retains_task_done_controls_and_candidate_output(
    runtime_context: RuntimeToolContext,
) -> None:
    tools = CapabilityToolAssembler().assemble(
        _resolved_spec("security:v1"),
        runtime_context,
    )

    async def invoke(tool_name: str, arguments: dict[str, object]) -> None:
        tool = next(tool for tool in tools if tool.name == tool_name)
        payload = json.dumps(arguments)
        await tool.on_invoke_tool(
            ToolContext(
                None,
                usage=Usage(),
                tool_name=tool_name,
                tool_call_id=f"test-{tool_name}",
                tool_arguments=payload,
                run_config=RunConfig(),
            ),
            payload,
        )

    await invoke("read_file", {"path": "src/value.py", "version": "current"})
    await invoke("review_file_done", {"reviewed_files": ["src/value.py"]})
    await invoke("task_done", {"summary": "Reviewed the complete frozen scope."})

    assert runtime_context.is_completed is True
    assert runtime_context.incomplete_review_files == ()
    assert isinstance(runtime_context.final_output(), CandidateFindingBatch)


@pytest.mark.parametrize(
    ("agent_reference", "expected"),
    (
        (
            "review-planner:v1",
            ("find_files", "grep", "read_file", "get_diff", "submit_review_plan", "finalize_plan"),
        ),
        (
            "review-verifier:v1",
            ("read_file", "get_diff", "verdict", "merge", "finalize_verdicts"),
        ),
    ),
)
def test_internal_roles_receive_only_their_frozen_tool_matrix(
    runtime_context: RuntimeToolContext,
    agent_reference: str,
    expected: tuple[str, ...],
) -> None:
    assert _tool_names(agent_reference, runtime_context) == expected
    assert "comment" not in expected
    assert "task_done" not in expected


def test_unknown_contract_version_fails_before_any_tool_is_returned(
    runtime_context: RuntimeToolContext,
) -> None:
    spec = _resolved_spec("correctness:v1")
    profile = replace(
        spec.capability_profile,
        builtin_tools=(ToolContractReference("comment", 99),),
    )
    invalid_spec = replace(spec, capability_profile=profile)

    with pytest.raises(PermanentAgentOutputError, match="comment:v99"):
        CapabilityToolAssembler().assemble(invalid_spec, runtime_context)


def test_missing_internal_role_implementation_fails_closed(
    runtime_context: RuntimeToolContext,
) -> None:
    production_context = replace(runtime_context, role_output_tools=())

    with pytest.raises(PermanentAgentOutputError, match="verdict:v1"):
        CapabilityToolAssembler().assemble(
            _resolved_spec("review-verifier:v1"),
            production_context,
        )


def test_legacy_comment_without_confidence_floor_fails_closed(
    runtime_context: RuntimeToolContext,
) -> None:
    spec = _resolved_spec("correctness:v1")
    invalid_spec = replace(spec, agent=replace(spec.agent, confidence_floor=None))

    with pytest.raises(PermanentAgentOutputError, match="confidence floor"):
        CapabilityToolAssembler().assemble(invalid_spec, runtime_context)

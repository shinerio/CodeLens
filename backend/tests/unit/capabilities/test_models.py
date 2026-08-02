import hashlib
from dataclasses import FrozenInstanceError, replace

import pytest

from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    CapabilityProfile,
    FrozenAgentExecutionSpec,
    FrozenSkillActivation,
    SkillPolicyReference,
    ToolContractReference,
    canonical_execution_payload,
    hydrate_execution_spec,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog


def _profile(*tools: ToolContractReference) -> CapabilityProfile:
    return CapabilityProfile(
        profile_id="legacy-reviewer",
        version=1,
        builtin_tools=tools,
        mcp_tools=(),
        is_read_only=True,
    )


def test_execution_fingerprint_changes_when_tool_contract_changes() -> None:
    agent = builtin_agent_catalog()["correctness:v1"]
    first_profile = _profile(ToolContractReference("comment", 1))
    second_profile = replace(
        first_profile,
        builtin_tools=(ToolContractReference("comment", 2),),
    )

    first = FrozenAgentExecutionSpec.create(
        agent=agent,
        capability_profile=first_profile,
        skill_policy=SkillPolicyReference("none", 1),
        prompt_content_hash="a" * 64,
        skills=(),
        execution_limits=AgentExecutionLimits.legacy_default(),
    )
    second = FrozenAgentExecutionSpec.create(
        agent=agent,
        capability_profile=second_profile,
        skill_policy=SkillPolicyReference("none", 1),
        prompt_content_hash="a" * 64,
        skills=(),
        execution_limits=AgentExecutionLimits.legacy_default(),
    )

    assert first.fingerprint != second.fingerprint


def test_profile_rejects_duplicate_contracts_and_write_access() -> None:
    duplicate = ToolContractReference("read_file", 1)
    with pytest.raises(ValueError, match="duplicate tool contracts"):
        _profile(duplicate, duplicate)
    with pytest.raises(ValueError, match="read-only"):
        CapabilityProfile("unsafe", 1, (duplicate,), (), False)


@pytest.mark.parametrize("content_hash", ["", "a" * 63, "z" * 64])
def test_frozen_skill_rejects_malformed_content_hashes(content_hash: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        FrozenSkillActivation(
            skill_id="python-review",
            version=1,
            content_hash=content_hash,
            activation_reason="python changed",
            instruction_text="Inspect Python changes.",
        )


def test_execution_spec_rejects_duplicate_skills() -> None:
    skill = FrozenSkillActivation(
        skill_id="python-review",
        version=1,
        content_hash="b" * 64,
        activation_reason="python changed",
        instruction_text="Inspect Python changes.",
    )
    with pytest.raises(ValueError, match="duplicate Skill activations"):
        FrozenAgentExecutionSpec.create(
            agent=builtin_agent_catalog()["correctness:v1"],
            capability_profile=_profile(ToolContractReference("read_file", 1)),
            skill_policy=SkillPolicyReference("none", 1),
            prompt_content_hash="a" * 64,
            skills=(skill, skill),
            execution_limits=AgentExecutionLimits.legacy_default(),
        )


def test_execution_fingerprint_is_independent_of_skill_input_order() -> None:
    skills = tuple(
        FrozenSkillActivation(
            skill_id=skill_id,
            version=1,
            content_hash=content_hash * 64,
            activation_reason="matched",
            instruction_text=f"Apply {skill_id}.",
        )
        for skill_id, content_hash in (("security-review", "a"), ("python-review", "b"))
    )
    values = {
        "agent": builtin_agent_catalog()["correctness:v1"],
        "capability_profile": _profile(ToolContractReference("read_file", 1)),
        "skill_policy": SkillPolicyReference("none", 1),
        "prompt_content_hash": "c" * 64,
        "execution_limits": AgentExecutionLimits.legacy_default(),
    }

    first = FrozenAgentExecutionSpec.create(skills=skills, **values)
    second = FrozenAgentExecutionSpec.create(skills=tuple(reversed(skills)), **values)

    assert first.fingerprint == second.fingerprint
    assert first.skills == tuple(sorted(first.skills))


def test_execution_values_are_immutable_and_limits_must_be_positive() -> None:
    reference = ToolContractReference("read_file", 1)
    with pytest.raises(FrozenInstanceError):
        reference.version = 2  # type: ignore[misc]
    with pytest.raises(ValueError, match="positive"):
        replace(AgentExecutionLimits.legacy_default(), max_tool_calls=0)


def test_legacy_limits_are_frozen_to_the_approved_values() -> None:
    assert AgentExecutionLimits.legacy_default() == AgentExecutionLimits(
        max_turns=20,
        max_tool_calls=120,
        max_input_tokens=120_000,
        max_output_tokens=16_000,
        timeout_seconds=600.0,
        max_tool_result_bytes=1_048_576,
    )


def test_hydrate_execution_spec_reconstructs_the_frozen_runtime_values() -> None:
    prompt_text = "Frozen reviewer prompt."
    instruction_text = "Inspect Python changes."
    skill = FrozenSkillActivation(
        skill_id="python-review",
        version=1,
        content_hash=hashlib.sha256(instruction_text.encode()).hexdigest(),
        activation_reason="python changed",
        instruction_text=instruction_text,
    )
    spec = FrozenAgentExecutionSpec.create(
        agent=replace(
            builtin_agent_catalog()["correctness:v1"], prompt_template=prompt_text
        ),
        capability_profile=_profile(ToolContractReference("read_file", 1)),
        skill_policy=SkillPolicyReference("none", 1),
        prompt_content_hash=hashlib.sha256(prompt_text.encode()).hexdigest(),
        skills=(skill,),
        execution_limits=AgentExecutionLimits.legacy_default(),
    )
    spec_json = canonical_execution_payload(
        spec.agent,
        spec.capability_profile,
        spec.skill_policy,
        spec.prompt_content_hash,
        spec.skills,
        spec.execution_limits,
    ).decode()

    hydrated = hydrate_execution_spec(
        spec_json,
        prompt_text=prompt_text,
        skill_instruction_texts=(instruction_text,),
    )

    assert hydrated == spec


@pytest.mark.parametrize(
    ("prompt_text", "skill_text", "message"),
    (
        ("changed prompt", "Inspect Python changes.", "prompt bytes"),
        ("Frozen reviewer prompt.", "changed skill", "Skill bytes"),
    ),
)
def test_hydrate_execution_spec_rejects_changed_artifact_bytes(
    prompt_text: str, skill_text: str, message: str
) -> None:
    original_prompt = "Frozen reviewer prompt."
    original_skill = "Inspect Python changes."
    skill = FrozenSkillActivation(
        skill_id="python-review",
        version=1,
        content_hash=hashlib.sha256(original_skill.encode()).hexdigest(),
        activation_reason="python changed",
        instruction_text=original_skill,
    )
    spec = FrozenAgentExecutionSpec.create(
        agent=builtin_agent_catalog()["correctness:v1"],
        capability_profile=_profile(ToolContractReference("read_file", 1)),
        skill_policy=SkillPolicyReference("none", 1),
        prompt_content_hash=hashlib.sha256(original_prompt.encode()).hexdigest(),
        skills=(skill,),
        execution_limits=AgentExecutionLimits.legacy_default(),
    )
    spec_json = canonical_execution_payload(
        spec.agent,
        spec.capability_profile,
        spec.skill_policy,
        spec.prompt_content_hash,
        spec.skills,
        spec.execution_limits,
    ).decode()

    with pytest.raises(ValueError, match=message):
        hydrate_execution_spec(
            spec_json,
            prompt_text=prompt_text,
            skill_instruction_texts=(skill_text,),
        )

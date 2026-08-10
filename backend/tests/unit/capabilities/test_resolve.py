from dataclasses import replace

import pytest

from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    CapabilityProfile,
    ToolContractReference,
)
from codelens.capabilities.domain.skills import (
    SkillActivationFacts,
    SkillManifest,
    SkillPolicy,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog


def test_resolver_uses_only_the_profile_statically_bound_to_the_agent() -> None:
    agent = builtin_agent_catalog()["review-verifier:v2"]

    spec = CapabilityResolver.testing().resolve(
        agent=agent,
        prompt_content_hash="a" * 64,
        facts=SkillActivationFacts.empty(),
        execution_limits=AgentExecutionLimits.default(),
    )

    assert spec.agent is agent
    assert spec.capability_profile.reference == "verifier:v2"
    assert tuple(tool.name for tool in spec.capability_profile.builtin_tools) == (
        "read_file",
        "get_diff",
        "verdict",
        "merge",
        "finalize_verdicts",
    )


def test_agent_cannot_resolve_an_unavailable_or_different_profile() -> None:
    agent = builtin_agent_catalog()["correctness:v2"]
    unavailable = replace(agent, capability_profile_ref="security-review:v99")

    with pytest.raises(ValueError, match="Capability Profile is unavailable"):
        CapabilityResolver.testing().resolve(
            agent=unavailable,
            prompt_content_hash="a" * 64,
            facts=SkillActivationFacts.empty(),
            execution_limits=AgentExecutionLimits.default(),
        )


def test_agent_cannot_resolve_an_unavailable_skill_policy() -> None:
    agent = replace(
        builtin_agent_catalog()["correctness:v2"],
        skill_policy_ref="unavailable:v2",
    )

    with pytest.raises(ValueError, match="Skill Policy is unavailable"):
        CapabilityResolver.testing().resolve(
            agent=agent,
            prompt_content_hash="a" * 64,
            facts=SkillActivationFacts.empty(),
            execution_limits=AgentExecutionLimits.default(),
        )


def test_resolver_returns_a_deterministic_frozen_spec() -> None:
    agent = builtin_agent_catalog()["security:v2"]
    resolver = CapabilityResolver.testing()

    first = resolver.resolve(
        agent=agent,
        prompt_content_hash="b" * 64,
        facts=SkillActivationFacts.empty(),
        execution_limits=AgentExecutionLimits.default(),
    )
    second = resolver.resolve(
        agent=agent,
        prompt_content_hash="b" * 64,
        facts=SkillActivationFacts.empty(),
        execution_limits=AgentExecutionLimits.default(),
    )

    assert first == second
    assert len(first.fingerprint) == 64
    assert first.skill_policy.reference == "none:v2"
    assert first.skills == ()


def test_resolver_activates_only_skills_from_the_static_policy() -> None:
    profile = CapabilityProfile(
        "python-reviewer",
        1,
        (
            ToolContractReference("read_file", 1),
            ToolContractReference("comment", 2),
        ),
        (),
        True,
    )
    policy = SkillPolicy(
        "python-skills",
        1,
        (
            SkillManifest(
                skill_id="python-async-safety",
                version=1,
                content_hash="d" * 64,
                required_tools=(ToolContractReference("read_file", 1),),
                activation_languages=("python",),
                instruction_text="Inspect asynchronous cancellation safety.",
            ),
        ),
    )
    agent = replace(
        builtin_agent_catalog()["security:v2"],
        capability_profile_ref=profile.reference,
        skill_policy_ref=policy.reference,
    )
    resolver = CapabilityResolver(
        {profile.reference: profile},
        {policy.reference: policy},
    )

    spec = resolver.resolve(
        agent=agent,
        prompt_content_hash="e" * 64,
        facts=SkillActivationFacts(("python",), ("src/worker.py",)),
        execution_limits=AgentExecutionLimits.default(),
    )

    assert tuple(skill.skill_id for skill in spec.skills) == ("python-async-safety",)


def test_resolver_rejects_a_profile_with_the_wrong_output_tool_version() -> None:
    profile = CapabilityProfile(
        "reviewer",
        2,
        (ToolContractReference("comment", 3),),
        (),
        True,
    )

    with pytest.raises(ValueError, match="tool version"):
        CapabilityResolver(
            {profile.reference: profile},
            {"none:v2": SkillPolicy("none", 2, ())},
        ).resolve(
            agent=builtin_agent_catalog()["correctness:v2"],
            prompt_content_hash="a" * 64,
            facts=SkillActivationFacts.empty(),
            execution_limits=AgentExecutionLimits.default(),
        )

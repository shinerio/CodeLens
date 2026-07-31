from codelens.capabilities.application.skill_activation import SkillActivationResolver
from codelens.capabilities.domain.models import (
    AgentExecutionLimits,
    CapabilityProfile,
    FrozenAgentExecutionSpec,
    SkillPolicyReference,
)
from codelens.capabilities.domain.skills import SkillActivationFacts, SkillPolicy
from codelens.capabilities.infrastructure.builtin_profiles import (
    FORBIDDEN_REVIEW_TOOL_NAMES,
    builtin_capability_profiles,
    builtin_skill_policies,
)
from codelens.reviewer_catalog.domain.models import AgentVersion


class CapabilityResolver:
    """Resolve only the Capability and Skill references frozen on an Agent version."""

    def __init__(
        self,
        profiles: dict[str, CapabilityProfile],
        skill_policies: dict[str, SkillPolicy],
        skill_activation: SkillActivationResolver | None = None,
    ) -> None:
        self._profiles = dict(profiles)
        self._skill_policies = dict(skill_policies)
        self._skill_activation = skill_activation or SkillActivationResolver()

    @classmethod
    def testing(cls) -> "CapabilityResolver":
        """Build a deterministic resolver from the production built-in registries."""

        return cls(builtin_capability_profiles(), builtin_skill_policies())

    def resolve(
        self,
        *,
        agent: AgentVersion,
        prompt_content_hash: str,
        facts: SkillActivationFacts,
        execution_limits: AgentExecutionLimits,
    ) -> FrozenAgentExecutionSpec:
        """Freeze one Agent execution or reject permanent catalog configuration errors."""

        try:
            profile = self._profiles[agent.capability_profile_ref]
        except KeyError as error:
            raise ValueError("Capability Profile is unavailable") from error
        try:
            skill_policy = self._skill_policies[agent.skill_policy_ref]
        except KeyError as error:
            raise ValueError("Skill Policy is unavailable") from error
        if not profile.is_read_only:
            raise ValueError("Review Capability Profile must be read-only")
        if {tool.name for tool in profile.builtin_tools} & FORBIDDEN_REVIEW_TOOL_NAMES:
            raise ValueError("Capability Profile contains a forbidden Review tool")
        self._validate_output_contract(agent, profile)
        skills = self._skill_activation.resolve(
            policy=skill_policy,
            profile=profile,
            facts=facts,
        )
        return FrozenAgentExecutionSpec.create(
            agent=agent,
            capability_profile=profile,
            skill_policy=SkillPolicyReference(skill_policy.policy_id, skill_policy.version),
            prompt_content_hash=prompt_content_hash,
            skills=skills,
            execution_limits=execution_limits,
        )

    @staticmethod
    def _validate_output_contract(
        agent: AgentVersion,
        profile: CapabilityProfile,
    ) -> None:
        expected_output_tool = {
            "1": ("comment", 1),
            "2": ("comment", 2),
            "review-plan:1": ("submit_review_plan", 1),
            "resolution:1": ("submit_resolution", 1),
            "verification:1": ("submit_verification", 1),
        }.get(agent.output_contract_version)
        if expected_output_tool is None:
            raise ValueError("Agent output contract is unavailable")
        available = {(tool.name, tool.version) for tool in profile.builtin_tools}
        if expected_output_tool not in available:
            raise ValueError("Capability Profile tool version does not match Agent output")

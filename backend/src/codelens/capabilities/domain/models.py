import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from codelens.reviewer_catalog.domain.models import AgentVersion

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")


def _require_sha256(value: str, label: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 hash")


@dataclass(frozen=True, order=True)
class ToolContractReference:
    """Identify one stable model-visible CodeLens tool contract."""

    name: str
    version: int

    def __post_init__(self) -> None:
        _require_identifier(self.name, "Tool contract name")
        if self.version < 1:
            raise ValueError("Tool contract version must be positive")

    @property
    def reference(self) -> str:
        """Return the stable versioned contract identity."""

        return f"{self.name}:v{self.version}"


class McpToolBindingView(Protocol):
    """Expose immutable MCP binding fields needed by execution fingerprinting."""

    contract: ToolContractReference
    server_id: str
    remote_tool_name: str
    schema_hash: str
    snapshot_scoped: bool
    data_egress: bool
    timeout_seconds: float
    max_result_bytes: int


@dataclass(frozen=True)
class CapabilityProfile:
    """Allow only a fixed, read-only set of stable tools for one Agent role."""

    profile_id: str
    version: int
    builtin_tools: tuple[ToolContractReference, ...]
    mcp_tools: tuple[McpToolBindingView, ...]
    is_read_only: bool

    def __post_init__(self) -> None:
        _require_identifier(self.profile_id, "Capability Profile identifier")
        if self.version < 1:
            raise ValueError("Capability Profile version must be positive")
        if not self.is_read_only:
            raise ValueError("Review Capability Profiles must be read-only")
        references = tuple(tool.reference for tool in self.builtin_tools) + tuple(
            binding.contract.reference for binding in self.mcp_tools
        )
        if len(references) != len(set(references)):
            raise ValueError("Capability Profile contains duplicate tool contracts")

    @property
    def reference(self) -> str:
        """Return the immutable Profile identity used by Reviewer versions."""

        return f"{self.profile_id}:v{self.version}"


@dataclass(frozen=True, order=True)
class SkillPolicyReference:
    """Identify one host-controlled declarative Skill policy."""

    policy_id: str
    version: int

    def __post_init__(self) -> None:
        _require_identifier(self.policy_id, "Skill Policy identifier")
        if self.version < 1:
            raise ValueError("Skill Policy version must be positive")

    @property
    def reference(self) -> str:
        """Return the stable versioned Skill Policy identity."""

        return f"{self.policy_id}:v{self.version}"


@dataclass(frozen=True, order=True)
class FrozenSkillActivation:
    """Freeze one instruction-only Skill and the host-derived activation reason."""

    skill_id: str
    version: int
    content_hash: str
    activation_reason: str
    instruction_text: str

    def __post_init__(self) -> None:
        _require_identifier(self.skill_id, "Skill identifier")
        if self.version < 1:
            raise ValueError("Skill version must be positive")
        _require_sha256(self.content_hash, "Skill content hash")
        if not self.activation_reason:
            raise ValueError("Skill activation reason cannot be empty")
        if not self.instruction_text:
            raise ValueError("Skill instruction text cannot be empty")


@dataclass(frozen=True)
class AgentExecutionLimits:
    """Freeze every per-Agent resource limit used by runtime enforcement."""

    max_turns: int
    max_tool_calls: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    max_tool_result_bytes: int

    def __post_init__(self) -> None:
        values = (
            self.max_turns,
            self.max_tool_calls,
            self.max_input_tokens,
            self.max_output_tokens,
            self.timeout_seconds,
            self.max_tool_result_bytes,
        )
        if any(value <= 0 for value in values):
            raise ValueError("Agent execution limits must be positive")

    @classmethod
    def legacy_default(cls) -> "AgentExecutionLimits":
        """Return the frozen resource envelope used for legacy migration tests."""

        return cls(20, 120, 120_000, 16_000, 600.0, 1_048_576)


@dataclass(frozen=True)
class FrozenAgentExecutionSpec:
    """Bind an Agent to immutable prompt, tools, Skills, and execution limits."""

    agent: AgentVersion
    capability_profile: CapabilityProfile
    skill_policy: SkillPolicyReference
    prompt_content_hash: str
    skills: tuple[FrozenSkillActivation, ...]
    execution_limits: AgentExecutionLimits
    fingerprint: str

    def __post_init__(self) -> None:
        _require_sha256(self.prompt_content_hash, "Prompt content hash")
        _require_sha256(self.fingerprint, "Execution fingerprint")
        if self.capability_profile.reference != self.agent.capability_profile_ref:
            raise ValueError("Agent is bound to a different Capability Profile")
        if self.skill_policy.reference != self.agent.skill_policy_ref:
            raise ValueError("Agent is bound to a different Skill Policy")

    @classmethod
    def create(
        cls,
        *,
        agent: AgentVersion,
        capability_profile: CapabilityProfile,
        skill_policy: SkillPolicyReference,
        prompt_content_hash: str,
        skills: tuple[FrozenSkillActivation, ...],
        execution_limits: AgentExecutionLimits,
    ) -> "FrozenAgentExecutionSpec":
        """Canonicalize frozen inputs and derive their deterministic fingerprint."""

        _require_sha256(agent.content_hash, "Agent content hash")
        _require_sha256(prompt_content_hash, "Prompt content hash")
        for binding in capability_profile.mcp_tools:
            _require_sha256(binding.schema_hash, "MCP schema hash")
        skill_identities = tuple((skill.skill_id, skill.version) for skill in skills)
        if len(skill_identities) != len(set(skill_identities)):
            raise ValueError("Execution spec contains duplicate Skill activations")
        canonical_skills = tuple(sorted(skills))
        fingerprint = hashlib.sha256(
            canonical_execution_payload(
                agent,
                capability_profile,
                skill_policy,
                prompt_content_hash,
                canonical_skills,
                execution_limits,
            )
        ).hexdigest()
        return cls(
            agent=agent,
            capability_profile=capability_profile,
            skill_policy=skill_policy,
            prompt_content_hash=prompt_content_hash,
            skills=canonical_skills,
            execution_limits=execution_limits,
            fingerprint=fingerprint,
        )


def canonical_execution_payload(
    agent: AgentVersion,
    capability_profile: CapabilityProfile,
    skill_policy: SkillPolicyReference,
    prompt_content_hash: str,
    skills: tuple[FrozenSkillActivation, ...],
    execution_limits: AgentExecutionLimits,
) -> bytes:
    """Serialize every execution-affecting identity with stable JSON ordering."""

    payload = {
        "agent": {
            "content_hash": agent.content_hash,
            "output_contract_version": agent.output_contract_version,
            "reference": agent.reference,
        },
        "capability_profile": {
            "builtin_tools": [tool.reference for tool in capability_profile.builtin_tools],
            "is_read_only": capability_profile.is_read_only,
            "mcp_tools": [
                {
                    "contract": binding.contract.reference,
                    "data_egress": binding.data_egress,
                    "max_result_bytes": binding.max_result_bytes,
                    "remote_tool_name": binding.remote_tool_name,
                    "schema_hash": binding.schema_hash,
                    "server_id": binding.server_id,
                    "snapshot_scoped": binding.snapshot_scoped,
                    "timeout_seconds": binding.timeout_seconds,
                }
                for binding in sorted(
                    capability_profile.mcp_tools,
                    key=lambda item: (
                        item.contract,
                        item.server_id,
                        item.remote_tool_name,
                    ),
                )
            ],
            "reference": capability_profile.reference,
        },
        "execution_limits": {
            "max_input_tokens": execution_limits.max_input_tokens,
            "max_output_tokens": execution_limits.max_output_tokens,
            "max_tool_calls": execution_limits.max_tool_calls,
            "max_tool_result_bytes": execution_limits.max_tool_result_bytes,
            "max_turns": execution_limits.max_turns,
            "timeout_seconds": execution_limits.timeout_seconds,
        },
        "prompt_content_hash": prompt_content_hash,
        "skill_policy": skill_policy.reference,
        "skills": [
            {
                "activation_reason": skill.activation_reason,
                "content_hash": skill.content_hash,
                "skill_id": skill.skill_id,
                "version": skill.version,
            }
            for skill in sorted(skills)
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

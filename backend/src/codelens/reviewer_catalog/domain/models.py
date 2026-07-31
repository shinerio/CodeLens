from dataclasses import dataclass
from enum import StrEnum


class AgentRole(StrEnum):
    """Classify one immutable Agent version by its DAG responsibility."""

    PLANNER = "planner"
    REVIEWER = "reviewer"
    RESOLVER = "resolver"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class ModelProfile:
    """Freeze provider-neutral model execution settings selected by evaluation."""

    profile_id: str
    model_id: str
    reasoning_effort: str | None
    max_output_tokens: int
    max_retries: int
    content_hash: str


@dataclass(frozen=True)
class AgentVersion:
    """Freeze one Reviewer prompt, model policy, capability set, and output contract."""

    agent_id: str
    version: int
    prompt_template: str
    model_profile_id: str
    output_contract_version: str
    timeout_seconds: float
    max_turns: int
    confidence_floor: float | None
    failure_policy: str
    content_hash: str
    role: AgentRole = AgentRole.REVIEWER
    prompt_key: str = "correctness"
    capability_profile_ref: str = "legacy-reviewer:v1"
    skill_policy_ref: str = "none:v1"
    dimensions: tuple[str, ...] = ()
    planner_eligible: bool = False
    is_public: bool = False
    is_legacy: bool = False

    @property
    def reference(self) -> str:
        """Return the canonical versioned catalog reference."""

        return f"{self.agent_id}:v{self.version}"

from dataclasses import dataclass

from codelens.workspace.domain.models import ReviewMode


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
    output_schema_version: str
    timeout_seconds: float
    max_turns: int
    token_budget: int
    confidence_floor: float
    failure_policy: str
    mode_support: tuple[ReviewMode, ...]
    content_hash: str


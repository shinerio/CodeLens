import hashlib
import json

from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.workspace.domain.models import ReviewMode

_RUNTIME_PROMPT_PLACEHOLDER = "Prompt template is loaded from the prompt catalog at runtime."


def correctness_agent() -> AgentVersion:
    """Return the immutable built-in correctness reviewer definition."""

    identity = {
        "agent_id": "correctness",
        "version": 1,
        "prompt_template": _RUNTIME_PROMPT_PLACEHOLDER,
        "model_profile_id": "balanced",
        "output_schema_version": "1",
        "timeout_seconds": 300.0,
        "max_turns": 100,
        "token_budget": 32_000,
        "confidence_floor": 0.7,
        "failure_policy": "fail_task",
        "mode_support": [mode.value for mode in ReviewMode],
    }
    content_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return AgentVersion(
        agent_id="correctness",
        version=1,
        prompt_template=_RUNTIME_PROMPT_PLACEHOLDER,
        model_profile_id="balanced",
        output_schema_version="1",
        timeout_seconds=300.0,
        max_turns=100,
        token_budget=32_000,
        confidence_floor=0.7,
        failure_policy="fail_task",
        mode_support=tuple(ReviewMode),
        content_hash=content_hash,
    )

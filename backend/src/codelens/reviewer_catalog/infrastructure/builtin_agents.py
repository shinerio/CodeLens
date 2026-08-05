import hashlib
import json
from collections.abc import Iterable
from typing import Any

from codelens.reviewer_catalog.domain.models import AgentRole, AgentVersion

_RUNTIME_PROMPT_PLACEHOLDER = "Prompt template is loaded from the prompt catalog at runtime."


def _agent(**identity: Any) -> AgentVersion:
    """Build one catalog entry and hash every execution-affecting identity field."""

    content_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return AgentVersion(**identity, content_hash=content_hash)


def _reviewer(
    *,
    agent_id: str,
    version: int,
    prompt_key: str,
    dimensions: tuple[str, ...],
    output_contract_version: str = "2",
    capability_profile_ref: str = "reviewer-comment-v2:v1",
    confidence_floor: float | None = None,
    failure_policy: str = "partial_team",
    planner_eligible: bool = True,
    is_public: bool = True,
    is_legacy: bool = False,
) -> AgentVersion:
    return _agent(
        agent_id=agent_id,
        version=version,
        role=AgentRole.REVIEWER,
        prompt_key=prompt_key,
        prompt_template=_RUNTIME_PROMPT_PLACEHOLDER,
        model_profile_id="balanced",
        output_contract_version=output_contract_version,
        capability_profile_ref=capability_profile_ref,
        skill_policy_ref="none:v1",
        timeout_seconds=300.0,
        max_turns=100,
        confidence_floor=confidence_floor,
        failure_policy=failure_policy,
        dimensions=dimensions,
        planner_eligible=planner_eligible,
        is_public=is_public,
        is_legacy=is_legacy,
    )


def _internal_agent(
    *,
    agent_id: str,
    role: AgentRole,
    prompt_key: str,
    output_contract_version: str,
    capability_profile_ref: str,
    failure_policy: str,
) -> AgentVersion:
    return _agent(
        agent_id=agent_id,
        version=1,
        role=role,
        prompt_key=prompt_key,
        prompt_template=_RUNTIME_PROMPT_PLACEHOLDER,
        model_profile_id="balanced",
        output_contract_version=output_contract_version,
        capability_profile_ref=capability_profile_ref,
        skill_policy_ref="none:v1",
        timeout_seconds=300.0,
        max_turns=100,
        confidence_floor=None,
        failure_policy=failure_policy,
        dimensions=(),
        planner_eligible=False,
        is_public=False,
        is_legacy=False,
    )


def _catalog_entries() -> Iterable[AgentVersion]:
    all_dimensions = (
        "correctness",
        "security",
        "reliability-concurrency",
        "contract-data",
        "architecture",
        "performance",
        "test-regression",
    )
    yield _reviewer(
        agent_id="correctness",
        version=1,
        prompt_key="correctness",
        dimensions=("correctness",),
        output_contract_version="1",
        capability_profile_ref="legacy-reviewer:v1",
        confidence_floor=0.7,
        failure_policy="fail_task",
        planner_eligible=False,
        is_public=False,
        is_legacy=True,
    )
    yield _reviewer(
        agent_id="correctness",
        version=2,
        prompt_key="correctness-v2",
        dimensions=("correctness",),
    )
    yield _reviewer(
        agent_id="security",
        version=1,
        prompt_key="security",
        dimensions=("security",),
    )
    yield _reviewer(
        agent_id="reliability-concurrency",
        version=1,
        prompt_key="reliability-concurrency",
        dimensions=("reliability-concurrency",),
    )
    yield _reviewer(
        agent_id="contract-data",
        version=1,
        prompt_key="contract-data",
        dimensions=("contract-data",),
    )
    yield _reviewer(
        agent_id="architecture",
        version=1,
        prompt_key="architecture",
        dimensions=("architecture",),
    )
    yield _reviewer(
        agent_id="performance",
        version=1,
        prompt_key="performance",
        dimensions=("performance",),
    )
    yield _reviewer(
        agent_id="test-regression",
        version=1,
        prompt_key="test-regression",
        dimensions=("test-regression",),
    )
    yield _reviewer(
        agent_id="general",
        version=1,
        prompt_key="general",
        dimensions=all_dimensions,
        planner_eligible=False,
    )
    yield _internal_agent(
        agent_id="review-planner",
        role=AgentRole.PLANNER,
        prompt_key="review-planner",
        output_contract_version="review-plan:1",
        capability_profile_ref="planner:v1",
        failure_policy="fail_task",
    )
    yield _internal_agent(
        agent_id="review-verifier",
        role=AgentRole.VERIFIER,
        prompt_key="review-verdict",
        output_contract_version="verdict:1",
        capability_profile_ref="verifier:v1",
        failure_policy="partial_task",
    )


def builtin_agent_catalog() -> dict[str, AgentVersion]:
    """Return every immutable built-in Agent version keyed by canonical reference.

    New reviewers remain an internal catalog in Phase 1. Callers that create
    Reviews continue to expose only the legacy correctness reviewer until the
    orchestration phase installs the new selection protocol.
    """

    agents = tuple(_catalog_entries())
    return {agent.reference: agent for agent in agents}


def correctness_agent() -> AgentVersion:
    """Return the immutable legacy correctness reviewer for compatibility."""

    return builtin_agent_catalog()["correctness:v1"]

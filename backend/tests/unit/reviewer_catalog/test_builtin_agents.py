from pathlib import Path

from codelens.reviewer_catalog.domain.models import AgentRole
from codelens.reviewer_catalog.infrastructure.builtin_agents import (
    builtin_agent_catalog,
    correctness_agent,
)


def test_catalog_contains_the_approved_public_reviewers() -> None:
    catalog = builtin_agent_catalog()
    public = {reference for reference, agent in catalog.items() if agent.is_public}

    assert public == {
        "correctness:v2",
        "security:v1",
        "reliability-concurrency:v1",
        "contract-data:v1",
        "architecture:v1",
        "performance:v1",
        "test-regression:v1",
        "general:v1",
    }


def test_legacy_correctness_is_not_planner_eligible() -> None:
    legacy = builtin_agent_catalog()["correctness:v1"]

    assert legacy.is_legacy is True
    assert legacy.is_public is False
    assert legacy.planner_eligible is False
    assert legacy.output_contract_version == "1"
    assert legacy.confidence_floor == 0.7
    assert correctness_agent() == legacy


def test_catalog_roles_and_planner_visibility_are_frozen() -> None:
    catalog = builtin_agent_catalog()
    specialists = {
        reference
        for reference, agent in catalog.items()
        if agent.role is AgentRole.REVIEWER
        and reference not in {"correctness:v1", "general:v1"}
    }

    assert all(catalog[reference].planner_eligible for reference in specialists)
    assert catalog["general:v1"].planner_eligible is False
    assert catalog["general:v1"].dimensions == (
        "correctness",
        "security",
        "reliability-concurrency",
        "contract-data",
        "architecture",
        "performance",
        "test-regression",
    )
    assert catalog["review-planner:v1"].role is AgentRole.PLANNER
    assert catalog["review-resolver:v1"].role is AgentRole.RESOLVER
    assert catalog["review-verifier:v1"].role is AgentRole.VERIFIER
    assert all(
        not catalog[reference].is_public
        for reference in (
            "review-planner:v1",
            "review-resolver:v1",
            "review-verifier:v1",
        )
    )


def test_catalog_keys_and_prompt_files_match_agent_identity() -> None:
    catalog = builtin_agent_catalog()
    prompt_root = Path(__file__).resolve().parents[4] / "prompts"

    assert all(reference == agent.reference for reference, agent in catalog.items())
    for agent in catalog.values():
        for locale in ("en", "zh-CN"):
            prompt = (prompt_root / agent.prompt_key / f"{locale}.md").read_text(
                encoding="utf-8"
            )
            assert prompt.strip()


def test_content_hash_changes_when_execution_identity_changes() -> None:
    catalog = builtin_agent_catalog()

    assert len({agent.content_hash for agent in catalog.values()}) == len(catalog)
    assert all(len(agent.content_hash) == 64 for agent in catalog.values())

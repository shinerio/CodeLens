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
        "security:v2",
        "reliability-concurrency:v2",
        "contract-data:v2",
        "architecture:v2",
        "performance:v2",
        "test-regression:v2",
        "general:v2",
    }


def test_catalog_contains_only_v2_agents() -> None:
    catalog = builtin_agent_catalog()

    assert all(agent.version == 2 for agent in catalog.values())
    assert correctness_agent() == catalog["correctness:v2"]


def test_catalog_roles_and_planner_visibility_are_frozen() -> None:
    catalog = builtin_agent_catalog()
    specialists = {
        reference
        for reference, agent in catalog.items()
        if agent.role is AgentRole.REVIEWER and reference != "general:v2"
    }

    assert all(catalog[reference].planner_eligible for reference in specialists)
    assert catalog["general:v2"].planner_eligible is True
    assert catalog["general:v2"].dimensions == (
        "correctness",
        "security",
        "reliability-concurrency",
        "contract-data",
        "architecture",
        "performance",
        "test-regression",
    )
    assert catalog["review-planner:v2"].role is AgentRole.PLANNER
    assert catalog["review-verifier:v2"].role is AgentRole.VERIFIER
    assert all(
        not catalog[reference].is_public
        for reference in (
            "review-planner:v2",
            "review-verifier:v2",
        )
    )


def test_catalog_keys_and_prompt_files_match_agent_identity() -> None:
    catalog = builtin_agent_catalog()
    prompt_root = Path(__file__).resolve().parents[4] / "prompts"

    assert all(reference == agent.reference for reference, agent in catalog.items())
    for agent in catalog.values():
        for locale in ("en", "zh-CN"):
            prompt = (prompt_root / agent.prompt_key / f"{locale}.md").read_text(encoding="utf-8")
            assert prompt.strip()


def test_content_hash_changes_when_execution_identity_changes() -> None:
    catalog = builtin_agent_catalog()

    assert len({agent.content_hash for agent in catalog.values()}) == len(catalog)
    assert all(len(agent.content_hash) == 64 for agent in catalog.values())

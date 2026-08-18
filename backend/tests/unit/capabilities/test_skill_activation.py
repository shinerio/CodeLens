from dataclasses import FrozenInstanceError

import pytest

from codelens.capabilities.application.skill_activation import SkillActivationResolver
from codelens.capabilities.domain.models import CapabilityProfile, ToolContractReference
from codelens.capabilities.domain.skills import (
    SkillActivationFacts,
    SkillManifest,
    SkillPolicy,
)


def profile_with_only(tool_name: str) -> CapabilityProfile:
    return CapabilityProfile(
        profile_id="test-read-only",
        version=1,
        builtin_tools=(ToolContractReference(tool_name, 1),),
        mcp_tools=(),
        is_read_only=True,
    )


def manifest(
    *,
    skill_id: str = "python-async-safety",
    language: str = "python",
    required_tool: str = "read_file",
    content_hash: str = "a" * 64,
) -> SkillManifest:
    return SkillManifest(
        skill_id=skill_id,
        version=1,
        content_hash=content_hash,
        required_tools=(ToolContractReference(required_tool, 1),),
        activation_languages=(language,),
        instruction_text="Inspect asynchronous resource and cancellation safety.",
    )


def test_skill_cannot_require_a_capability_outside_the_profile() -> None:
    django = manifest(
        skill_id="python-django-review",
        required_tool="symbol_search",
    )

    with pytest.raises(ValueError, match="required capability"):
        SkillActivationResolver().resolve(
            policy=SkillPolicy("reviewer-default", 1, (django,)),
            profile=profile_with_only("read_file"),
            facts=SkillActivationFacts(languages=("python",), changed_paths=("app/models.py",)),
        )


def test_skill_activation_is_deterministic_and_sorted() -> None:
    policy = SkillPolicy(
        "reviewer-default",
        1,
        (
            manifest(skill_id="z-python", content_hash="b" * 64),
            manifest(skill_id="a-python", content_hash="c" * 64),
        ),
    )
    resolver = SkillActivationResolver()
    facts = SkillActivationFacts(
        languages=("python",), changed_paths=("src/worker.py", "src/api.py")
    )

    first = resolver.resolve(
        policy=policy,
        profile=profile_with_only("read_file"),
        facts=facts,
    )
    second = resolver.resolve(
        policy=policy,
        profile=profile_with_only("read_file"),
        facts=SkillActivationFacts(
            languages=("python",), changed_paths=("src/api.py", "src/worker.py")
        ),
    )

    assert first == second
    assert tuple(item.skill_id for item in first) == ("a-python", "z-python")
    assert tuple(item.content_hash for item in first) == ("c" * 64, "b" * 64)


def test_skill_is_absent_when_host_facts_do_not_match() -> None:
    activations = SkillActivationResolver().resolve(
        policy=SkillPolicy("reviewer-default", 1, (manifest(),)),
        profile=profile_with_only("read_file"),
        facts=SkillActivationFacts(
            languages=("typescript",), changed_paths=("frontend/src/App.tsx",)
        ),
    )

    assert activations == ()


def test_frozen_activation_contains_instruction_text_and_content_identity() -> None:
    activations = SkillActivationResolver().resolve(
        policy=SkillPolicy("reviewer-default", 1, (manifest(),)),
        profile=profile_with_only("read_file"),
        facts=SkillActivationFacts(languages=("python",), changed_paths=("src/worker.py",)),
    )

    activation = activations[0]
    assert activation.instruction_text.startswith("Inspect asynchronous")
    assert activation.content_hash == "a" * 64
    assert not hasattr(activation, "execute")
    with pytest.raises(FrozenInstanceError):
        activation.instruction_text = "Run a script."  # type: ignore[misc]


def test_skill_values_reject_invalid_content_and_duplicate_manifests() -> None:
    with pytest.raises(ValueError, match="content hash"):
        manifest(content_hash="not-a-hash")
    with pytest.raises(ValueError, match="instruction text"):
        SkillManifest(
            skill_id="empty-skill",
            version=1,
            content_hash="a" * 64,
            required_tools=(),
            activation_languages=("python",),
            instruction_text=" ",
        )
    duplicate = manifest()
    with pytest.raises(ValueError, match="duplicate Skill"):
        SkillPolicy("reviewer-default", 1, (duplicate, duplicate))


def test_empty_facts_activate_nothing() -> None:
    assert SkillActivationFacts.empty() == SkillActivationFacts((), ())
    assert (
        SkillActivationResolver().resolve(
            policy=SkillPolicy("reviewer-default", 1, (manifest(),)),
            profile=profile_with_only("read_file"),
            facts=SkillActivationFacts.empty(),
        )
        == ()
    )

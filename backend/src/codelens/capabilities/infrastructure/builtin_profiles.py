from codelens.capabilities.domain.models import (
    CapabilityProfile,
    ToolContractReference,
)
from codelens.capabilities.domain.skills import SkillPolicy

FORBIDDEN_REVIEW_TOOL_NAMES = frozenset(
    {
        "shell",
        "write_file",
        "apply_patch",
        "git",
        "network",
        "load_skill",
        "discover_tools",
    }
)


def _tools(*entries: tuple[str, int]) -> tuple[ToolContractReference, ...]:
    return tuple(ToolContractReference(name, version) for name, version in entries)


def _profile(
    profile_id: str,
    tools: tuple[ToolContractReference, ...],
) -> CapabilityProfile:
    forbidden = {tool.name for tool in tools} & FORBIDDEN_REVIEW_TOOL_NAMES
    if forbidden:
        raise ValueError("Capability Profile contains a forbidden Review tool")
    return CapabilityProfile(profile_id, 1, tools, (), True)


def builtin_capability_profiles() -> dict[str, CapabilityProfile]:
    """Return immutable role-specific allowlists for every built-in Agent."""

    evidence = (
        ("find_files", 1),
        ("grep", 1),
        ("read_file", 1),
        ("get_diff", 1),
    )
    profiles = (
        _profile(
            "legacy-reviewer",
            _tools(
                *evidence,
                ("comment", 1),
                ("review_file_done", 1),
                ("task_done", 1),
            ),
        ),
        _profile(
            "reviewer-comment-v2",
            _tools(
                *evidence,
                ("comment", 2),
                ("review_file_done", 1),
                ("task_done", 1),
            ),
        ),
        _profile(
            "planner",
            _tools(*evidence, ("submit_review_plan", 1), ("finalize_plan", 1)),
        ),
        _profile(
            "resolver",
            _tools(("read_file", 1), ("get_diff", 1), ("submit_resolution", 1)),
        ),
        _profile("verifier", _tools(*evidence, ("submit_verification", 1))),
    )
    return {profile.reference: profile for profile in profiles}


def builtin_skill_policies() -> dict[str, SkillPolicy]:
    """Return declarative built-in Skill policies; Phase 2 starts with no Skills."""

    policy = SkillPolicy("none", 1, ())
    return {policy.reference: policy}

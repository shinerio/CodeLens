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
    return CapabilityProfile(profile_id, 2, tools, (), True)


def builtin_capability_profiles() -> dict[str, CapabilityProfile]:
    """Return immutable role-specific allowlists for every built-in Agent."""

    evidence = (
        ("find_files", 2),
        ("grep", 2),
        ("read_file", 2),
        ("get_diff", 2),
    )
    profiles = (
        _profile(
            "reviewer",
            _tools(
                *evidence,
                ("comment", 2),
                ("task_done", 2),
            ),
        ),
        _profile(
            "planner",
            _tools(*evidence, ("finalize_plan", 2)),
        ),
        _profile(
            "verifier",
            _tools(
                ("read_file", 2),
                ("get_diff", 2),
                ("verdict", 2),
                ("merge", 2),
                ("finalize_verdicts", 2),
            ),
        ),
    )
    return {profile.reference: profile for profile in profiles}


def builtin_skill_policies() -> dict[str, SkillPolicy]:
    """Return declarative built-in Skill policies; Phase 2 starts with no Skills."""

    policy = SkillPolicy("none", 2, ())
    return {policy.reference: policy}

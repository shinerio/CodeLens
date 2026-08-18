from codelens.capabilities.infrastructure.builtin_profiles import (
    FORBIDDEN_REVIEW_TOOL_NAMES,
    builtin_capability_profiles,
    builtin_skill_policies,
)


def _tool_names(profile_reference: str) -> tuple[str, ...]:
    profile = builtin_capability_profiles()[profile_reference]
    return tuple(binding.name for binding in profile.builtin_tools)


def test_builtin_profiles_expose_only_the_approved_tools() -> None:
    assert _tool_names("planner:v2") == (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "finalize_plan",
    )
    assert _tool_names("reviewer:v2") == (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "comment",
        "retract_comment",
        "task_done",
    )
    assert _tool_names("verifier:v2") == (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "verdict",
        "merge",
        "finalize_verdicts",
    )


def test_every_visible_tool_contract_is_v2() -> None:
    profiles = builtin_capability_profiles()

    assert all(tool.version == 2 for profile in profiles.values() for tool in profile.builtin_tools)


def test_every_builtin_profile_is_read_only_and_contains_no_forbidden_tool() -> None:
    profiles = builtin_capability_profiles()

    assert set(profiles) == {
        "reviewer:v2",
        "planner:v2",
        "verifier:v2",
    }
    assert all(profile.is_read_only for profile in profiles.values())
    assert (
        not {tool.name for profile in profiles.values() for tool in profile.builtin_tools}
        & FORBIDDEN_REVIEW_TOOL_NAMES
    )


def test_builtin_skill_policy_contains_only_the_no_skill_policy() -> None:
    policies = builtin_skill_policies()

    assert tuple(policies) == ("none:v2",)
    assert policies["none:v2"].reference == "none:v2"

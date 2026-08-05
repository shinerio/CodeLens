from codelens.capabilities.infrastructure.builtin_profiles import (
    FORBIDDEN_REVIEW_TOOL_NAMES,
    builtin_capability_profiles,
    builtin_skill_policies,
)


def _tool_names(profile_reference: str) -> tuple[str, ...]:
    profile = builtin_capability_profiles()[profile_reference]
    return tuple(binding.name for binding in profile.builtin_tools)


def test_builtin_profiles_expose_only_the_approved_tools() -> None:
    assert _tool_names("planner:v1") == (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "submit_review_plan",
        "finalize_plan",
    )
    assert _tool_names("legacy-reviewer:v1") == (
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "comment",
        "review_file_done",
        "task_done",
    )
    assert _tool_names("reviewer-comment-v2:v1") == _tool_names("legacy-reviewer:v1")
    assert _tool_names("verifier:v1") == (
        "read_file",
        "get_diff",
        "verdict",
        "merge",
        "finalize_verdicts",
    )


def test_comment_contract_is_versioned_without_renaming_the_visible_tool() -> None:
    profiles = builtin_capability_profiles()
    legacy_comment = next(
        tool for tool in profiles["legacy-reviewer:v1"].builtin_tools if tool.name == "comment"
    )
    candidate_comment = next(
        tool
        for tool in profiles["reviewer-comment-v2:v1"].builtin_tools
        if tool.name == "comment"
    )

    assert legacy_comment.version == 1
    assert candidate_comment.version == 2


def test_every_builtin_profile_is_read_only_and_contains_no_forbidden_tool() -> None:
    profiles = builtin_capability_profiles()

    assert set(profiles) == {
        "legacy-reviewer:v1",
        "reviewer-comment-v2:v1",
        "planner:v1",
        "verifier:v1",
    }
    assert all(profile.is_read_only for profile in profiles.values())
    assert not {
        tool.name for profile in profiles.values() for tool in profile.builtin_tools
    } & FORBIDDEN_REVIEW_TOOL_NAMES


def test_builtin_skill_policy_contains_only_the_no_skill_policy() -> None:
    policies = builtin_skill_policies()

    assert tuple(policies) == ("none:v1",)
    assert policies["none:v1"].reference == "none:v1"


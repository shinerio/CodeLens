import json

import pytest

from codelens.workspace.domain.review_file_scope import (
    ReviewFileExclusionPolicy,
    ReviewFileExclusionReason,
    ReviewFileScopeResolver,
)


def test_policy_is_canonical_and_excludes_binary_by_default() -> None:
    policy = ReviewFileExclusionPolicy(
        suffixes=(".MAP", ".map", " .min.js "),
        path_regexes=(r"(^|/)generated/", r"(^|/)generated/"),
    )

    assert policy.suffixes == (".map", ".min.js")
    assert policy.path_regexes == (r"(^|/)generated/",)
    assert policy.exclude_binary is True
    assert json.loads(policy.canonical_json()) == {
        "exclude_binary": True,
        "path_regexes": [r"(^|/)generated/"],
        "suffixes": [".map", ".min.js"],
    }
    assert len(policy.policy_hash) == 64


def test_policy_rejects_empty_suffix_and_invalid_regex() -> None:
    with pytest.raises(ValueError, match="suffix"):
        ReviewFileExclusionPolicy(suffixes=(" ",))
    with pytest.raises(ValueError, match=r"path_regexes\[0\]"):
        ReviewFileExclusionPolicy(path_regexes=("[",))


def test_resolver_uses_all_exclusion_facts_for_review_and_context() -> None:
    policy = ReviewFileExclusionPolicy(
        suffixes=(".lock",),
        path_regexes=(r"(^|/)generated/",),
    )

    scope = ReviewFileScopeResolver().resolve(
        candidate_review_paths=(
            "src/main.py",
            "assets/logo.png",
            "vendor/generated/code.py",
            "package.LOCK",
        ),
        candidate_context_paths=("docs/guide.md", "vendor/generated/context.md"),
        policy=policy,
        git_ignored_paths=("vendor/generated/code.py",),
        instruction_excluded_paths=("vendor/generated/code.py",),
        binary_paths=("assets/logo.png",),
    )

    assert scope.review_paths == ("src/main.py",)
    assert scope.context_paths == ("docs/guide.md",)
    assert [item.path for item in scope.exclusions] == [
        "assets/logo.png",
        "package.LOCK",
        "vendor/generated/code.py",
        "vendor/generated/context.md",
    ]
    generated = next(item for item in scope.exclusions if item.path == "vendor/generated/code.py")
    assert generated.reasons == (
        ReviewFileExclusionReason.GITIGNORE,
        ReviewFileExclusionReason.REPOSITORY_INSTRUCTION,
        ReviewFileExclusionReason.USER_REGEX,
    )
    assert len(scope.scope_hash) == 64


def test_binary_fact_is_always_excluded() -> None:
    scope = ReviewFileScopeResolver().resolve(
        candidate_review_paths=("asset.bin",),
        candidate_context_paths=(),
        policy=ReviewFileExclusionPolicy(),
        binary_paths=("asset.bin",),
    )

    assert scope.review_paths == ()
    assert scope.exclusions[0].reasons == (ReviewFileExclusionReason.BINARY,)

from pathlib import Path

from codelens.review.infrastructure.i18n_prompt_loader import I18nPromptLoader

PROMPT_ROOT = Path(__file__).parents[4] / "prompts"
EXPECTED_BUNDLE_FILES = {
    "review-policy.md",
    "review-workflow.md",
    "tools.json",
}


def test_system_prompt_locales_use_the_minimal_bundle_file_set() -> None:
    for locale in ("en", "zh-CN"):
        bundle_files = {
            path.name for path in (PROMPT_ROOT / "sys" / locale).iterdir() if path.is_file()
        }

        assert bundle_files == EXPECTED_BUNDLE_FILES


def test_repository_policy_describes_prefetched_root_rules_without_duplication() -> None:
    loader = I18nPromptLoader.load(PROMPT_ROOT)
    english = loader.get("en")
    chinese = loader.get("zh-CN")

    assert "Root-level `AGENTS.md` and `REVIEW.md`, when present, are already loaded" in (
        english.review_policy
    )
    assert "No other repository rules are preloaded" in english.review_policy
    assert "根目录中存在的 `AGENTS.md` 和 `REVIEW.md` 已默认加载" in chinese.review_policy
    assert "其他仓库规则均未预加载" in chinese.review_policy
    assert english.review_policy.count("`instruction_loader`") == 1
    assert chinese.review_policy.count("`instruction_loader`") == 1
    assert "instruction_loader" not in english.review_workflow
    assert "instruction_loader" not in chinese.review_workflow
    assert "get_diff" not in english.review_workflow
    assert "get_diff" not in chinese.review_workflow
    assert english.review_workflow.count("`task_done`") == 1
    assert chinese.review_workflow.count("`task_done`") == 1

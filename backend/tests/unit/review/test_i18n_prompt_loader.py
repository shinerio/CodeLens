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


def test_repository_policy_describes_complete_prefetched_rules_without_loader_tool() -> None:
    loader = I18nPromptLoader.load(PROMPT_ROOT)
    english = loader.get("en")
    chinese = loader.get("zh-CN")

    assert "repository_instructions" in english.review_policy
    assert "complete frozen repository rules" in english.review_policy
    assert "repository_instructions" in chinese.review_policy
    assert "完整冻结仓库规则" in chinese.review_policy
    assert "instruction_loader" not in english.review_policy
    assert "instruction_loader" not in chinese.review_policy
    assert "instruction_loader" not in english.review_workflow
    assert "instruction_loader" not in chinese.review_workflow
    assert "Apply the rules mapped to each file" in english.review_workflow
    assert "直接应用映射到每个文件的规则" in chinese.review_workflow
    assert "never request a shell or invent another tool" in english.review_workflow
    assert "不得请求 Shell，也不得发明其他工具" in chinese.review_workflow
    assert "get_diff" not in english.review_workflow
    assert "get_diff" not in chinese.review_workflow
    assert english.review_workflow.count("`task_done`") == 1
    assert chinese.review_workflow.count("`task_done`") == 1

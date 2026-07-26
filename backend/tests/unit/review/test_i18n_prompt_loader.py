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


def test_repository_policy_describes_complete_prefetched_rules_and_review_workflow() -> None:
    loader = I18nPromptLoader.load(PROMPT_ROOT)
    english = loader.get("en")
    chinese = loader.get("zh-CN")

    assert "repository_instructions" in english.review_policy
    assert "complete frozen repository rules" in english.review_policy
    assert "repository_instructions" in chinese.review_policy
    assert "完整冻结仓库规则" in chinese.review_policy
    assert "Apply the rules mapped to each file" in english.review_workflow
    assert "直接应用映射到每个文件的规则" in chinese.review_workflow
    assert "never request a shell or invent another tool" in english.review_workflow
    assert "不得请求 Shell，也不得发明其他工具" in chinese.review_workflow
    assert "`review_file_done`" in english.review_workflow
    assert "`review_file_done`" in chinese.review_workflow
    assert "missing_evidence_files" in english.review_workflow
    assert "missing_evidence_files" in chinese.review_workflow
    assert "undeclared_files" in english.review_workflow
    assert "undeclared_files" in chinese.review_workflow
    assert "Do not include unchanged diff context" in english.tools["comment"].description
    assert "side=old" in english.tools["comment"].description
    assert "不得包含未修改的 diff 上下文行" in chinese.tools["comment"].description
    assert "side=old" in chinese.tools["comment"].description
    assert "narrow path or pattern" in english.tools["find_files"].description
    assert "缩小 path 或细化 pattern" in chinese.tools["find_files"].description
    assert "file_pattern" in english.tools["grep"].description
    assert "file_pattern" in chinese.tools["grep"].description
    assert "narrow pattern, path, or file_pattern" in english.tools["grep"].description
    assert "缩小 pattern、path 或细化 file_pattern" in chinese.tools["grep"].description
    assert "may both be omitted" in english.tools["read_file"].description
    assert "可以同时省略" in chinese.tools["read_file"].description

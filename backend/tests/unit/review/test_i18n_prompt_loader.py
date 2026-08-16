from pathlib import Path

from codelens.review.infrastructure.i18n_prompt_loader import I18nPromptLoader

PROMPT_ROOT = Path(__file__).parents[4] / "prompts"
EXPECTED_BUNDLE_FILES = {
    "checkpoint-compaction.md",
    "review-policy.md",
    "review-workflow.md",
    "review-feedback.md",
    "tool-not-found.md",
    "tool-loop-warning.md",
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
    assert "直接应用映射到每个文件的 `repository_instructions`" in chinese.review_workflow
    assert "never request a shell or invent another tool" in english.review_workflow
    assert "不得请求 Shell，也不得发明其他工具" in chinese.review_workflow
    assert "missing_review_files" in english.review_workflow
    assert "missing_review_files" in chinese.review_workflow
    assert (
        "trigger -> changed-code mechanism -> concrete harmful outcome" in english.review_workflow
    )
    assert "触发条件 → 变更代码的错误机制 → 具体危害" in chinese.review_workflow
    assert "do not report" in english.review_workflow
    assert "不要上报" in chinese.review_workflow
    assert "without diff markers or unchanged context" in english.tools["comment"].description
    assert "side=old" in english.tools["comment"].description
    assert "不含 diff 标记和未变更上下文" in chinese.tools["comment"].description
    assert "side=old" in chinese.tools["comment"].description
    assert "retract_comment" in english.review_workflow
    assert "retract_comment" in chinese.review_workflow
    assert "candidate_ids" in english.tools["retract_comment"].description
    assert "does not fall on an actual changed diff line" in english.review_feedback
    assert "没有落在本次实际变更的 diff 行" in chinese.review_feedback
    assert "evidence IDs" in english.checkpoint_compaction
    assert "证据 ID" in chinese.checkpoint_compaction
    assert "untrusted" in english.checkpoint_compaction
    assert "不可信" in chinese.checkpoint_compaction
    assert "task objective" in english.checkpoint_compaction
    assert "任务目标" in chinese.checkpoint_compaction
    assert "coverage" in english.checkpoint_compaction
    assert "覆盖进度" in chinese.checkpoint_compaction
    assert "JSON or XML" not in english.checkpoint_compaction
    assert "JSON 或 XML" not in chinese.checkpoint_compaction
    assert "{tool_name}" in english.tool_not_found
    assert "{available_tools}" in english.tool_not_found
    assert "{tool_name}" in chinese.tool_not_found
    assert "{available_tools}" in chinese.tool_not_found
    assert "recursively matches basenames" in english.tools["find_files"].description
    assert "递归匹配 basename" in chinese.tools["find_files"].description
    assert "file_pattern" in english.tools["grep"].description
    assert "file_pattern" in chinese.tools["grep"].description
    assert "mode=literal|regex" in english.tools["grep"].description
    assert "mode=literal|regex" in chinese.tools["grep"].description
    assert "start_line" in english.tools["read_file"].description
    assert "end_line" in english.tools["read_file"].description
    assert "start_line" in chinese.tools["read_file"].description
    assert "end_line" in chinese.tools["read_file"].description
    assert "Omit cursor" in english.tools["get_diff"].description
    assert "省略 cursor" in chinese.tools["get_diff"].description


def test_planner_and_verifier_prompts_define_focused_decision_boundaries() -> None:
    loader = I18nPromptLoader.load(PROMPT_ROOT)
    english = loader.get("en")
    chinese = loader.get("zh-CN")

    assert "submit_review_plan" not in english.tools
    assert "submit_review_plan" not in chinese.tools
    assert "General alone or at least two specialists" in english.tools["finalize_plan"].description
    assert "General 单独运行或至少两个专项 Reviewer" in chinese.tools["finalize_plan"].description

    for locale in ("en", "zh-CN"):
        verifier = (PROMPT_ROOT / "review-verdict" / f"{locale}.md").read_text(encoding="utf-8")
        assert "weak" in verifier
        assert "inferred" in verifier
    english_verifier = (PROMPT_ROOT / "review-verdict" / "en.md").read_text(encoding="utf-8")
    chinese_verifier = (PROMPT_ROOT / "review-verdict" / "zh-CN.md").read_text(encoding="utf-8")
    assert "cannot establish that a defect exists" in english_verifier
    assert "无法建立缺陷确实存在" in chinese_verifier

from pathlib import Path

from codelens.instruction_policy.application.resolver import InstructionResolver
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser
from codelens.instruction_policy.infrastructure.structured_skip import StructuredSkipMatcher


def test_resolves_ordered_instruction_chain_even_when_rule_file_is_ignored(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("REVIEW.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Repository conventions", encoding="utf-8")
    (tmp_path / "REVIEW.md").write_text("Root review", encoding="utf-8")
    target_dir = tmp_path / "src" / "payments"
    target_dir.mkdir(parents=True)
    (tmp_path / "src" / "REVIEW.md").write_text("Source rules", encoding="utf-8")
    (target_dir / "REVIEW.md").write_text("Payment rules", encoding="utf-8")
    (target_dir / "payment.py.review.md").write_text("File rules", encoding="utf-8")
    (target_dir / "payment.py").write_text("pass\n", encoding="utf-8")

    resolved = InstructionResolver(MarkdownInstructionParser()).resolve(
        tmp_path,
        "src/payments/payment.py",
    )

    assert [document.relative_path for document in resolved.documents] == [
        "AGENTS.md",
        "REVIEW.md",
        "src/REVIEW.md",
        "src/payments/REVIEW.md",
        "src/payments/payment.py.review.md",
    ]


def test_parses_frontmatter_and_skip_heading(tmp_path: Path) -> None:
    (tmp_path / "REVIEW.md").write_text(
        "---\nexclude:\n  - generated/**\n---\n"
        "## Skip\n- vendor/**\n- Explain why generated clients are noisy\n",
        encoding="utf-8",
    )

    resolved = InstructionResolver(MarkdownInstructionParser()).resolve(tmp_path, "src/app.py")

    assert resolved.excludes == ("generated/**", "vendor/**")
    assert len(resolved.warnings) == 1


def test_scopes_nested_excludes_to_rule_directory(tmp_path: Path) -> None:
    rule_dir = tmp_path / "src" / "payments"
    rule_dir.mkdir(parents=True)
    (rule_dir / "REVIEW.md").write_text(
        "---\nexclude:\n  - generated/**\n---\nPayment rules\n",
        encoding="utf-8",
    )

    resolved = InstructionResolver(MarkdownInstructionParser()).resolve(
        tmp_path,
        "src/payments/api.py",
    )

    assert resolved.excludes == ("src/payments/generated/**",)


def test_structured_skip_matches_only_resolved_path_rules(tmp_path: Path) -> None:
    (tmp_path / "REVIEW.md").write_text(
        "---\nexclude:\n  - generated/**\n---\n",
        encoding="utf-8",
    )
    instructions = InstructionResolver(MarkdownInstructionParser()).resolve(
        tmp_path,
        "generated/api.py",
    )

    matcher = StructuredSkipMatcher()

    assert matcher.excludes("generated/api.py", instructions)
    assert not matcher.excludes("src/api.py", instructions)

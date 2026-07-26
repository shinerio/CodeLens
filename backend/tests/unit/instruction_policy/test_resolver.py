from dataclasses import dataclass
from pathlib import Path

from codelens.instruction_policy.application.resolver import InstructionResolver
from codelens.instruction_policy.domain.models import InstructionLineLimits
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser
from codelens.instruction_policy.infrastructure.structured_skip import StructuredSkipMatcher


@dataclass
class _MutableLineLimitsProvider:
    limits: InstructionLineLimits

    def get_line_limits(self) -> InstructionLineLimits:
        return self.limits


def test_resolves_ordered_instruction_chain_even_when_rule_file_is_ignored(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("REVIEW.md\n", encoding="utf-8")
    (tmp_path / "aGeNtS.Md").write_text("Repository conventions", encoding="utf-8")
    (tmp_path / "rEvIeW.mD").write_text("Root review", encoding="utf-8")
    target_dir = tmp_path / "src" / "payments"
    target_dir.mkdir(parents=True)
    (tmp_path / "src" / "AGENTS.MD").write_text("Source conventions", encoding="utf-8")
    (tmp_path / "src" / "Review.md").write_text("Source rules", encoding="utf-8")
    (target_dir / "agents.md").write_text("Payment conventions", encoding="utf-8")
    (target_dir / "REVIEW.MD").write_text("Payment rules", encoding="utf-8")
    (target_dir / "payment.py.ReViEw.Md").write_text("File rules", encoding="utf-8")
    (target_dir / "payment.py").write_text("pass\n", encoding="utf-8")

    resolved = InstructionResolver(MarkdownInstructionParser()).resolve(
        tmp_path,
        "src/payments/payment.py",
    )

    assert [document.relative_path for document in resolved.documents] == [
        "aGeNtS.Md",
        "rEvIeW.mD",
        "src/AGENTS.MD",
        "src/Review.md",
        "src/payments/agents.md",
        "src/payments/REVIEW.MD",
        "src/payments/payment.py.ReViEw.Md",
    ]
    assert [document.kind for document in resolved.documents] == [
        "agents",
        "review",
        "agents",
        "review",
        "agents",
        "review",
        "file_review",
    ]
    assert [document.scope_path for document in resolved.documents] == [
        "",
        "",
        "src",
        "src",
        "src/payments",
        "src/payments",
        "src/payments/payment.py",
    ]
    assert [document.precedence for document in resolved.documents] == [0, 1, 2, 3, 4, 5, 6]
    assert len(resolved.chains) == 1
    assert resolved.chains[0].target_path == "src/payments/payment.py"
    assert resolved.chains[0].rule_paths == tuple(
        document.relative_path for document in resolved.documents
    )


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


def test_root_instruction_uses_the_more_permissive_line_limit(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    target_dir = tmp_path / "src"
    target_dir.mkdir()
    (target_dir / "app.py").write_text("pass\n", encoding="utf-8")

    resolved = InstructionResolver(
        MarkdownInstructionParser(),
        line_limits=InstructionLineLimits(root_max_lines=3, nested_max_lines=2),
    ).resolve(tmp_path, "src/app.py")

    assert [document.relative_path for document in resolved.documents] == ["AGENTS.md"]


def test_nested_instruction_rejects_content_above_its_line_limit(tmp_path: Path) -> None:
    target_dir = tmp_path / "src"
    target_dir.mkdir()
    (target_dir / "REVIEW.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (target_dir / "app.py").write_text("pass\n", encoding="utf-8")

    resolver = InstructionResolver(
        MarkdownInstructionParser(),
        line_limits=InstructionLineLimits(root_max_lines=3, nested_max_lines=2),
    )

    try:
        resolver.resolve(tmp_path, "src/app.py")
    except ValueError as error:
        assert str(error) == "instruction document src/REVIEW.md exceeds the 2 line limit"
    else:
        raise AssertionError("nested instruction above the line limit must be rejected")


def test_resolver_reloads_line_limits_for_each_resolution(tmp_path: Path) -> None:
    target_dir = tmp_path / "src"
    target_dir.mkdir()
    (target_dir / "AGENTS.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    provider = _MutableLineLimitsProvider(InstructionLineLimits(3, 3))
    resolver = InstructionResolver(
        MarkdownInstructionParser(),
        line_limits_provider=provider,
    )

    resolver.resolve(tmp_path, "src/app.py")
    provider.limits = InstructionLineLimits(3, 2)

    try:
        resolver.resolve(tmp_path, "src/app.py")
    except ValueError as error:
        assert str(error) == "instruction document src/AGENTS.md exceeds the 2 line limit"
    else:
        raise AssertionError("updated line limits must affect the next resolution")

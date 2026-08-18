import ast
from dataclasses import fields
from pathlib import Path

from codelens import __version__
from codelens.reviewer_catalog.domain.models import AgentVersion

_BACKEND_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "codelens"
_FORBIDDEN_CAPABILITY_DOMAIN_IMPORTS = frozenset(
    {"agents", "fastapi", "mcp", "openai", "sqlalchemy"}
)


def test_package_version() -> None:
    assert __version__ == "0.2.0"


def test_capabilities_domain_has_no_provider_or_framework_imports() -> None:
    capability_domain = _BACKEND_SOURCE_ROOT / "capabilities" / "domain"
    imported_roots: set[str] = set()

    for source_path in capability_domain.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_roots.add(node.module.partition(".")[0])

    assert imported_roots.isdisjoint(_FORBIDDEN_CAPABILITY_DOMAIN_IMPORTS)


def test_reviewer_catalog_stores_capability_identities_as_references_only() -> None:
    agent_fields = {field.name: field.type for field in fields(AgentVersion)}

    assert agent_fields["capability_profile_ref"] is str
    assert agent_fields["skill_policy_ref"] is str

    reviewer_catalog = _BACKEND_SOURCE_ROOT / "reviewer_catalog"
    for source_path in reviewer_catalog.rglob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        assert "codelens.capabilities" not in source

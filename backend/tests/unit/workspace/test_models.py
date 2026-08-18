from codelens.workspace.domain.models import BranchScope, SnapshotManifest
from codelens.workspace.domain.review_file_scope import ReviewFileScope


def test_branch_scope_carries_base_and_target_refs() -> None:
    scope = BranchScope(
        base_ref="origin/main",
        target_ref="feature/invoice-rounding",
        include_workspace_changes=False,
    )

    assert scope.base_ref == "origin/main"
    assert scope.target_ref == "feature/invoice-rounding"


def test_manifest_separates_targets_from_context() -> None:
    manifest = SnapshotManifest(
        review_scope=ReviewFileScope.include_all(
            ("src/payment.py",),
            ("src/payment.py", "tests/test_payment.py"),
        ),
    )

    assert manifest.is_review_path("src/payment.py")
    assert not manifest.is_review_path("tests/test_payment.py")
    assert manifest.is_context("tests/test_payment.py")

from codelens.workspace.domain.models import BranchScope, ReviewMode, SnapshotManifest


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
        target_paths=("src/payment.py",),
        context_paths=("src/payment.py", "tests/test_payment.py"),
        excluded_paths=(),
    )

    assert manifest.is_target("src/payment.py")
    assert not manifest.is_target("tests/test_payment.py")
    assert manifest.is_context("tests/test_payment.py")


def test_review_mode_value_is_stable() -> None:
    assert ReviewMode.REVIEW.value == "review"

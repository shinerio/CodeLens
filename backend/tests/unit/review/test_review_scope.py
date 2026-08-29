"""Unit tests for build_review_files in review_scope."""

from pathlib import Path

import pytest

from codelens.review.application.review_scope import (
    ReviewScopeError,
    build_review_files,
)
from codelens.workspace.domain.models import (
    ChangedHunk,
    ChangeIndex,
    RepositoryFingerprint,
    ReviewFileChange,
    ReviewFileScope,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotManifest,
    TaskWorktree,
)

_WORKTREE = TaskWorktree(
    worktree_id="wt-1",
    task_id="review-1",
    repository_common_dir_hash="a" * 64,
    root=Path("/tmp/review-1"),
    head_oid="b" * 40,
    ownership_token_hash="c" * 64,
)
_FINGERPRINT = RepositoryFingerprint(
    head_sha="b" * 40,
    index_hash="d" * 64,
    worktree_hash="e" * 64,
)
_TARGET = ReviewTarget(base_oid="a" * 40, head_oid="b" * 40, overlay_hash=None)


def _snapshot(
    review_paths: tuple[str, ...],
    files: tuple[ReviewFileChange, ...],
    hunks: tuple[ChangedHunk, ...],
    whitespace_only_paths: tuple[str, ...] = (),
) -> ReviewSnapshot:
    scope = ReviewFileScope.include_all(review_paths=review_paths)
    manifest = SnapshotManifest(review_scope=scope)
    return ReviewSnapshot(
        snapshot_id="snapshot-1",
        worktree=_WORKTREE,
        target=_TARGET,
        fingerprint=_FINGERPRINT,
        manifest=manifest,
        change_index=ChangeIndex(
            hunks=hunks,
            files=files,
            whitespace_only_paths=whitespace_only_paths,
        ),
    )


def _hunk(path: str, start: int, end: int, side: str) -> ChangedHunk:
    return ChangedHunk(
        hunk_id=f"{path}\0{side}\0{start}\0{end}\0abc",
        path=path,
        start_line=start,
        end_line=end,
        side=side,
        excerpt_hash="abc",
    )


def test_whitespace_only_targets_are_silently_excluded() -> None:
    """Targets filtered as whitespace-only must not raise ReviewScopeError."""

    snapshot = _snapshot(
        review_paths=("real.py", "indent_only.py", "crlf_only.py"),
        files=(ReviewFileChange("real.py", "modified"),),
        hunks=(
            _hunk("real.py", 1, 1, "old"),
            _hunk("real.py", 1, 1, "new"),
        ),
        whitespace_only_paths=("indent_only.py", "crlf_only.py"),
    )
    result = build_review_files(snapshot, max_files=10, max_ranges=10)
    assert [f.path for f in result] == ["real.py"]


def test_genuinely_missing_target_raises() -> None:
    """A target absent from both files and whitespace_only_paths is an error."""

    snapshot = _snapshot(
        review_paths=("real.py", "missing.py"),
        files=(ReviewFileChange("real.py", "modified"),),
        hunks=(
            _hunk("real.py", 1, 1, "old"),
            _hunk("real.py", 1, 1, "new"),
        ),
    )
    with pytest.raises(ReviewScopeError, match="no immutable file change metadata"):
        build_review_files(snapshot, max_files=10, max_ranges=10)


def test_all_targets_covered() -> None:
    """Normal case with every target present in change_index.files."""

    snapshot = _snapshot(
        review_paths=("added.py", "modified.py"),
        files=(
            ReviewFileChange("added.py", "added"),
            ReviewFileChange("modified.py", "modified"),
        ),
        hunks=(
            _hunk("added.py", 1, 1, "new"),
            _hunk("modified.py", 1, 1, "old"),
            _hunk("modified.py", 1, 1, "new"),
        ),
    )
    result = build_review_files(snapshot, max_files=10, max_ranges=10)
    assert {f.path for f in result} == {"added.py", "modified.py"}

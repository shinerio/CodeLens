from dataclasses import dataclass
from pathlib import PurePosixPath

from codelens.workspace.domain.models import ReviewFileChangeType, ReviewSnapshot


class ReviewScopeError(ValueError):
    """Reject an incomplete or unsafe model-visible Review scope."""


class ReviewScopeLimitError(ReviewScopeError):
    """Reject a Review scope that exceeds a configured product limit."""


@dataclass(frozen=True)
class ReviewLineRange:
    """Expose one immutable changed range where a Finding may be submitted."""

    start_line: int
    end_line: int

    def as_payload(self) -> dict[str, int]:
        return {"start_line": self.start_line, "end_line": self.end_line}


@dataclass(frozen=True)
class ReviewFileInput:
    """Expose only stable file scope needed by the model's initial request."""

    path: str
    change_type: ReviewFileChangeType
    old_ranges: tuple[ReviewLineRange, ...]
    new_ranges: tuple[ReviewLineRange, ...]
    old_path: str | None = None

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": self.path,
            "change_type": self.change_type,
            "old_ranges": [item.as_payload() for item in self.old_ranges],
            "new_ranges": [item.as_payload() for item in self.new_ranges],
        }
        if self.old_path is not None:
            payload["old_path"] = self.old_path
        return payload


def build_review_files(
    snapshot: ReviewSnapshot,
    *,
    max_files: int,
    max_ranges: int,
) -> tuple[ReviewFileInput, ...]:
    """Build the complete deterministic model scope from frozen file metadata."""

    if max_files <= 0 or max_ranges <= 0:
        raise ReviewScopeLimitError("Review scope limits must be positive")
    targets = set(snapshot.manifest.target_paths)
    active_changes = tuple(
        change
        for change in snapshot.change_index.files
        if change.path in targets or (change.old_path is not None and change.old_path in targets)
    )
    if len(active_changes) > max_files:
        raise ReviewScopeLimitError("Review file limit exceeded")
    paths = [change.path for change in active_changes]
    if len(paths) != len(set(paths)):
        raise ReviewScopeError("Review scope contains duplicate file metadata")

    covered_targets = {
        path
        for change in active_changes
        for path in (change.path, change.old_path)
        if path is not None
    }
    if not targets.issubset(covered_targets):
        raise ReviewScopeError("Review target has no immutable file change metadata")

    changes_by_path = {change.path: change for change in active_changes}
    ranges_by_path: dict[str, dict[str, set[tuple[int, int]]]] = {
        path: {"old": set(), "new": set()} for path in changes_by_path
    }
    for hunk in snapshot.change_index.hunks:
        if not _is_normalized_relative(hunk.path):
            raise ReviewScopeError("Review hunk path is unsafe")
        change = changes_by_path.get(hunk.path)
        if change is None:
            raise ReviewScopeError("Review hunk has no file metadata")
        if hunk.side == "new" and change.change_type == "deleted":
            raise ReviewScopeError("deleted Review file has a new-side range")
        if hunk.side == "old" and change.change_type == "added":
            raise ReviewScopeError("added Review file has an old-side range")
        ranges_by_path[hunk.path][hunk.side].add((hunk.start_line, hunk.end_line))

    range_count = sum(
        len(ranges)
        for ranges_by_side in ranges_by_path.values()
        for ranges in ranges_by_side.values()
    )
    if range_count > max_ranges:
        raise ReviewScopeLimitError("Review range limit exceeded")

    review_files: list[ReviewFileInput] = []
    for change in sorted(active_changes, key=lambda item: item.path):
        if not _is_normalized_relative(change.path):
            raise ReviewScopeError("Review file path is unsafe")
        if change.old_path is not None and not _is_normalized_relative(change.old_path):
            raise ReviewScopeError("Review old_path is unsafe")
        review_files.append(
            ReviewFileInput(
                path=change.path,
                change_type=change.change_type,
                old_path=change.old_path,
                old_ranges=tuple(
                    ReviewLineRange(start_line, end_line)
                    for start_line, end_line in sorted(ranges_by_path[change.path]["old"])
                ),
                new_ranges=tuple(
                    ReviewLineRange(start_line, end_line)
                    for start_line, end_line in sorted(ranges_by_path[change.path]["new"])
                ),
            )
        )
    return tuple(review_files)


def _is_normalized_relative(path: str) -> bool:
    if not path or "\0" in path or "\\" in path:
        return False
    candidate = PurePosixPath(path)
    return (
        not candidate.is_absolute()
        and ".." not in candidate.parts
        and candidate.as_posix() == path
    )

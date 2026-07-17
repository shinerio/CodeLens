from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal


class ReviewMode(StrEnum):
    """Stable execution modes shared across review task boundaries."""

    REVIEW = "review"
    FIX = "fix"


@dataclass(frozen=True)
class BranchScope:
    """Review changes between a merge base and a selected target ref."""

    base_ref: str
    target_ref: str
    include_workspace_changes: bool = False
    type: Literal["branch"] = "branch"


@dataclass(frozen=True)
class CommitScope:
    """Review changes from an explicit baseline commit to a target ref."""

    base_commit: str
    target_ref: str = "HEAD"
    include_workspace_changes: bool = False
    type: Literal["commit"] = "commit"


@dataclass(frozen=True)
class UncommittedScope:
    """Review a frozen overlay relative to the current checkout HEAD."""

    type: Literal["uncommitted"] = "uncommitted"


@dataclass(frozen=True)
class FullRepositoryScope:
    """Review every eligible path at a selected immutable target."""

    target_ref: str = "HEAD"
    include_workspace_changes: bool = False
    type: Literal["full"] = "full"


type ReviewScope = BranchScope | CommitScope | UncommittedScope | FullRepositoryScope


@dataclass(frozen=True)
class ExcludedPath:
    """Record why a repository-relative path was excluded from a snapshot."""

    path: str
    reason: str
    source: str | None = None


@dataclass(frozen=True)
class IgnoreResolution:
    """Partition candidate paths using Git-native ignore decisions."""

    included: tuple[str, ...]
    excluded: tuple[ExcludedPath, ...]


@dataclass(frozen=True)
class SnapshotEntry:
    """Record immutable metadata for one target, context, or instruction path."""

    path: str
    kind: Literal["file", "symlink", "deleted"]
    mode: int
    size_bytes: int
    content_hash: str
    symlink_target: str | None
    origin: Literal["target", "context", "instruction"]


@dataclass(frozen=True)
class SnapshotManifest:
    """Separate review targets from safe read-only context and control inputs."""

    target_paths: tuple[str, ...]
    context_paths: tuple[str, ...]
    excluded_paths: tuple[ExcludedPath, ...]
    instruction_paths: tuple[str, ...] = ()
    entries: tuple[SnapshotEntry, ...] = ()

    def is_target(self, path: str) -> bool:
        """Return whether a normalized repository path is a review target."""

        return path in self.target_paths

    def is_context(self, path: str) -> bool:
        """Return whether a normalized repository path is visible as context."""

        return path in self.context_paths


@dataclass(frozen=True)
class RepositoryFingerprint:
    """Identify a repository checkout state without storing source contents."""

    head_sha: str
    index_hash: str
    worktree_hash: str


@dataclass(frozen=True)
class ReviewTarget:
    """Freeze resolved Git object IDs and an optional captured overlay."""

    base_oid: str
    head_oid: str
    overlay_hash: str | None


@dataclass(frozen=True)
class OpaqueArtifact:
    """Identify hash-verified bytes without exposing a backing filesystem path."""

    reference: str
    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class CapturedReviewInput:
    """Carry a pinned target and optional immutable workspace overlay Artifact."""

    target: ReviewTarget
    overlay_artifact: OpaqueArtifact | None


@dataclass(frozen=True)
class TaskWorktree:
    """Identify an application-owned detached worktree and its ownership proof."""

    worktree_id: str
    task_id: str
    repository_common_dir_hash: str
    root: Path
    head_oid: str
    ownership_token_hash: str


@dataclass(frozen=True)
class ChangedHunk:
    """Locate an immutable changed range that may support a Finding."""

    hunk_id: str
    path: str
    start_line: int
    end_line: int
    side: Literal["old", "new"]
    excerpt_hash: str


@dataclass(frozen=True)
class ChangeIndex:
    """Provide deterministic containment checks for changed source ranges."""

    hunks: tuple[ChangedHunk, ...]

    def contains(self, path: str, start_line: int, end_line: int, side: str) -> bool:
        """Return whether a location is fully contained by a matching hunk."""

        return any(
            hunk.path == path
            and hunk.side == side
            and start_line >= hunk.start_line
            and end_line <= hunk.end_line
            for hunk in self.hunks
        )


@dataclass(frozen=True)
class SnapshotBuild:
    """Return a frozen Manifest plus its integrity fingerprint from an adapter."""

    manifest: SnapshotManifest
    fingerprint: RepositoryFingerprint
    manifest_hash: str


@dataclass(frozen=True)
class ReviewSnapshot:
    """Freeze the worktree, target, manifest, and change evidence for a review."""

    snapshot_id: str
    worktree: TaskWorktree
    target: ReviewTarget
    fingerprint: RepositoryFingerprint
    manifest: SnapshotManifest
    change_index: ChangeIndex
    manifest_hash: str = ""
    snapshot_artifact: OpaqueArtifact | None = None

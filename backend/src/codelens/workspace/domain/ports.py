from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codelens.workspace.domain.models import (
    OpaqueArtifact,
    RepositoryFingerprint,
    ReviewScope,
    TaskWorktree,
)


@dataclass(frozen=True)
class RepositoryInfo:
    """Expose validated repository metadata without leaking Git adapter types."""

    path: Path
    repository_id: str
    repository_realpath_hash: str
    git_common_dir_hash: str
    head_sha: str
    current_branch: str | None
    is_dirty: bool


@dataclass(frozen=True)
class ScopePlan:
    """Freeze resolved object IDs and candidate paths before task creation."""

    base_oid: str
    head_oid: str
    target_paths: tuple[str, ...]
    capture_workspace_overlay: bool
    warnings: tuple[str, ...] = ()


class WorkspaceGitPort(Protocol):
    """Read Git state through bounded asynchronous operations.

    Implementations must use argument-array process calls, enforce time/output limits,
    and never modify a user's working tree, index, branches, or tags in REVIEW mode.
    """

    async def inspect(self, repository: Path) -> RepositoryInfo:
        """Return validated metadata for a contained Git repository root."""

        raise NotImplementedError

    async def plan_scope(self, repository: Path, scope: ReviewScope) -> ScopePlan:
        """Resolve a review scope to immutable full Git object IDs."""

        raise NotImplementedError

    async def list_context_paths(self, repository: Path) -> tuple[str, ...]:
        """Return normalized repository-relative context candidates."""

        raise NotImplementedError

    async def fingerprint(self, repository: Path) -> RepositoryFingerprint:
        """Return a stable fingerprint for mutation detection."""

        raise NotImplementedError

    async def read_file_at_revision(self, repository: Path, revision: str, path: str) -> bytes:
        """Read one contained path from an already resolved revision."""

        raise NotImplementedError

    async def unified_diff(self, worktree: Path, base_oid: str) -> str:
        """Return a bounded unified diff from an owned worktree to a pinned base."""

        raise NotImplementedError


class RepositoryMetadataPort(Protocol):
    """Read repository identity and checkout state without exposing Git commands."""

    async def inspect(self, repository: Path) -> RepositoryInfo:
        """Return metadata for a repository path already contained by the application."""

        raise NotImplementedError


class ScopePlanningPort(Protocol):
    """Resolve mutable scope input to an immutable executable plan."""

    async def plan_scope(self, repository: Path, scope: ReviewScope) -> ScopePlan:
        """Return pinned OIDs and normalized target candidates for one scope."""

        raise NotImplementedError


class WorktreeRegistryPort(Protocol):
    """Persist authoritative ownership records for task worktrees."""

    async def register(self, worktree: TaskWorktree) -> None:
        """Create one ownership record after the checkout is provisioned."""

        raise NotImplementedError

    async def get(self, task_id: str) -> TaskWorktree | None:
        """Return the ownership record for a task when present."""

        raise NotImplementedError

    async def remove(self, task_id: str) -> None:
        """Delete one ownership record after scoped Git cleanup succeeds."""

        raise NotImplementedError

    async def list_all(self) -> tuple[TaskWorktree, ...]:
        """Return all records for restart reconciliation."""

        raise NotImplementedError


class InputArtifactPort(Protocol):
    """Persist captured input bytes behind opaque, hash-verified references."""

    async def write_bytes(self, payload: bytes) -> OpaqueArtifact:
        """Atomically persist bytes and return their opaque identity."""

        raise NotImplementedError

    async def read_bytes(self, reference: str, expected_hash: str) -> bytes:
        """Load bytes only after reference containment and hash verification."""

        raise NotImplementedError

    async def discard(self, reference: str) -> None:
        """Remove an unreferenced staging or rejected input Artifact."""

        raise NotImplementedError


class ReviewInputCapturePort(Protocol):
    """Read source checkout state only during pre-task input capture."""

    async def fingerprint(
        self,
        repository: Path,
        target_paths: tuple[str, ...],
    ) -> RepositoryFingerprint:
        """Fingerprint tracked, untracked, and applicable control input state."""

        raise NotImplementedError

    async def capture_overlay(self, repository: Path, target_paths: tuple[str, ...]) -> bytes:
        """Return a bounded canonical overlay payload for immutable persistence."""

        raise NotImplementedError


class OverlayMaterializerPort(Protocol):
    """Reconstruct a captured overlay inside one verified task worktree."""

    async def materialize(self, worktree: TaskWorktree, payload: bytes) -> None:
        """Apply tracked changes and contained entries without source checkout reads."""

        raise NotImplementedError


class WorktreeRecoveryPort(Protocol):
    """Reconcile durable ownership records with task worktree filesystem state."""

    async def is_present(self, worktree: TaskWorktree) -> bool:
        """Return whether the recorded checkout path currently exists."""

        raise NotImplementedError

    async def verify_ownership(self, worktree: TaskWorktree) -> None:
        """Validate an existing checkout against all ownership proofs."""

        raise NotImplementedError

    async def forget_missing(self, worktree: TaskWorktree, repository: Path) -> None:
        """Remove exact stale metadata for a recorded checkout that is absent."""

        raise NotImplementedError


class ReviewWorktreePort(Protocol):
    """Create and remove only detached worktrees owned by a review task."""

    async def create(
        self,
        task_id: str,
        repository: Path,
        head_oid: str,
    ) -> TaskWorktree:
        """Create a detached task worktree at an immutable head object ID."""

        raise NotImplementedError

    async def verify_ownership(self, worktree: TaskWorktree) -> None:
        """Fail closed unless durable and on-disk ownership proofs agree."""

        raise NotImplementedError

    async def remove_owned(self, worktree: TaskWorktree) -> None:
        """Remove exactly one verified CodeLens-owned worktree."""

        raise NotImplementedError

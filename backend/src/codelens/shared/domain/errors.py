class DomainError(Exception):
    """Base class for stable domain failures exposed through interface adapters."""

    code = "domain_error"


class InvalidRepositoryError(DomainError):
    """Raised when a repository fails containment or Git validation."""

    code = "invalid_repository"


class SnapshotStaleError(DomainError):
    """Raised when repository inputs change while a snapshot is captured."""

    code = "snapshot_stale"


class WorktreeOwnershipError(DomainError):
    """Raised when CodeLens cannot prove ownership of a task worktree."""

    code = "worktree_ownership"


class WorktreeMutatedError(DomainError):
    """Raised when a read-only review changes its frozen worktree."""

    code = "worktree_mutated"

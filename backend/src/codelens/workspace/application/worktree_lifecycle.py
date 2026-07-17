from dataclasses import dataclass
from pathlib import Path

from codelens.workspace.domain.models import CapturedReviewInput, TaskWorktree
from codelens.workspace.domain.ports import (
    InputArtifactPort,
    OverlayMaterializerPort,
    ReviewWorktreePort,
    WorktreeRecoveryPort,
    WorktreeRegistryPort,
)


@dataclass(frozen=True)
class WorktreeRecoveryInput:
    """Provide the immutable target and trusted repository metadata needed for repair."""

    repository: Path
    captured: CapturedReviewInput


class ReviewWorktreeLifecycle:
    """Provision an owned worktree and reconstruct only a frozen input overlay."""

    def __init__(
        self,
        *,
        worktrees: ReviewWorktreePort,
        artifacts: InputArtifactPort,
        materializer: OverlayMaterializerPort,
    ) -> None:
        self._worktrees = worktrees
        self._artifacts = artifacts
        self._materializer = materializer

    async def create(
        self,
        task_id: str,
        repository: Path,
        captured: CapturedReviewInput,
    ) -> TaskWorktree:
        """Create a detached checkout and apply the hash-verified captured bytes."""

        worktree = await self._worktrees.create(task_id, repository, captured.target.head_oid)
        try:
            if captured.overlay_artifact is not None:
                artifact = captured.overlay_artifact
                payload = await self._artifacts.read_bytes(
                    artifact.reference,
                    artifact.content_hash,
                )
                await self._materializer.materialize(worktree, payload)
            return worktree
        except BaseException:
            await self._worktrees.remove_owned(worktree)
            raise

    async def remove_owned(self, worktree: TaskWorktree) -> None:
        """Remove one verified task worktree through the scoped ownership port."""

        await self._worktrees.remove_owned(worktree)

    async def verify_ownership(self, worktree: TaskWorktree) -> None:
        """Verify all ownership proofs for one existing task worktree."""

        await self._worktrees.verify_ownership(worktree)


class ReviewWorktreeRecoveryService:
    """Reconcile only registered CodeLens worktrees during singleton startup."""

    def __init__(
        self,
        *,
        lifecycle: ReviewWorktreeLifecycle,
        registry: WorktreeRegistryPort,
        recovery: WorktreeRecoveryPort,
    ) -> None:
        self._lifecycle = lifecycle
        self._registry = registry
        self._recovery = recovery

    async def reconcile(
        self,
        active: dict[str, WorktreeRecoveryInput],
    ) -> dict[str, TaskWorktree]:
        """Keep valid active checkouts, reconstruct missing ones, and remove owned orphans."""

        recovered: dict[str, TaskWorktree] = {}
        records = {record.task_id: record for record in await self._registry.list_all()}
        for task_id, record in records.items():
            recovery_input = active.get(task_id)
            if recovery_input is None:
                if await self._recovery.is_present(record):
                    await self._lifecycle.remove_owned(record)
                else:
                    message = "missing orphan worktree requires trusted repository metadata"
                    raise RuntimeError(message)
                continue
            if await self._recovery.is_present(record):
                await self._recovery.verify_ownership(record)
                recovered[task_id] = record
                continue
            await self._recovery.forget_missing(record, recovery_input.repository)
            recovered[task_id] = await self._lifecycle.create(
                task_id,
                recovery_input.repository,
                recovery_input.captured,
            )

        for task_id, recovery_input in active.items():
            if task_id in records:
                continue
            recovered[task_id] = await self._lifecycle.create(
                task_id,
                recovery_input.repository,
                recovery_input.captured,
            )
        return recovered

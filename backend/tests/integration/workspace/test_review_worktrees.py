import asyncio
import json
from pathlib import Path

import pytest

from codelens.shared.domain.errors import WorktreeOwnershipError
from codelens.workspace.application.worktree_lifecycle import (
    ReviewWorktreeLifecycle,
    ReviewWorktreeRecoveryService,
    WorktreeRecoveryInput,
)
from codelens.workspace.domain.models import CapturedReviewInput, ReviewTarget, TaskWorktree
from codelens.workspace.infrastructure.git_cli import CommandResult, GitCli
from codelens.workspace.infrastructure.git_overlay import GitOverlayMaterializer
from codelens.workspace.infrastructure.git_worktrees import (
    GitReviewWorktreeManager,
    RepositoryLockRegistry,
)
from codelens.workspace.infrastructure.input_artifacts import FilesystemInputArtifactStore


class InMemoryWorktreeRegistry:
    def __init__(self) -> None:
        self.records: dict[str, TaskWorktree] = {}

    async def register(self, worktree: TaskWorktree) -> None:
        self.records[worktree.task_id] = worktree

    async def get(self, task_id: str) -> TaskWorktree | None:
        return self.records.get(task_id)

    async def remove(self, task_id: str) -> None:
        self.records.pop(task_id, None)

    async def list_all(self) -> tuple[TaskWorktree, ...]:
        return tuple(self.records.values())


class RecordingGitCli(GitCli):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ...]] = []

    async def run(
        self,
        repository: Path,
        *args: str,
        stdin: bytes | None = None,
        ok_codes: tuple[int, ...] = (0,),
    ) -> CommandResult:
        self.calls.append(args)
        return await super().run(
            repository,
            *args,
            stdin=stdin,
            ok_codes=ok_codes,
        )


async def _create_feature(git: GitCli, repository: Path, branch: str, filename: str) -> str:
    await git.run(repository, "checkout", "main")
    await git.run(repository, "checkout", "-b", branch)
    (repository / filename).write_text(f"BRANCH = '{branch}'\n", encoding="utf-8")
    await git.run(repository, "add", filename)
    await git.run(repository, "commit", "-m", f"add {branch}")
    oid = (await git.run(repository, "rev-parse", "HEAD")).stdout.decode("ascii").strip()
    await git.run(repository, "checkout", "main")
    return oid


async def _path_exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


def _tamper_marker(marker_path: Path) -> None:
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["ownership_token_hash"] = "0" * 64
    marker_path.write_text(json.dumps(marker), encoding="utf-8")


async def test_creates_distinct_owned_worktrees_and_preserves_user_worktree(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    git = RecordingGitCli()
    feature_a_oid = await _create_feature(git, git_repository, "feature-a", "feature_a.py")
    feature_b_oid = await _create_feature(git, git_repository, "feature-b", "feature_b.py")
    user_worktree = tmp_path / "user-worktree"
    await git.run(
        git_repository,
        "worktree",
        "add",
        "--detach",
        str(user_worktree),
        "main",
    )
    registry = InMemoryWorktreeRegistry()
    manager = GitReviewWorktreeManager(
        data_dir=tmp_path / "app-data",
        git=git,
        registry=registry,
        locks=RepositoryLockRegistry(),
    )

    first, second = await asyncio.gather(
        manager.create("review-a", git_repository, feature_a_oid),
        manager.create("review-b", git_repository, feature_b_oid),
    )

    assert first.root != second.root
    assert first.head_oid == feature_a_oid
    assert second.head_oid == feature_b_oid
    assert await _path_exists(first.root / "feature_a.py")
    assert not await _path_exists(first.root / "feature_b.py")
    assert await _path_exists(second.root / "feature_b.py")

    await asyncio.gather(manager.remove_owned(first), manager.remove_owned(second))

    worktree_list = await git.run(git_repository, "worktree", "list", "--porcelain")
    assert str(user_worktree) in worktree_list.stdout.decode("utf-8")
    assert await _path_exists(user_worktree)
    assert registry.records == {}
    assert not any(call[:2] == ("worktree", "prune") for call in git.calls)


async def test_quarantines_worktree_when_ownership_marker_does_not_match(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    git = RecordingGitCli()
    head_oid = (await git.run(git_repository, "rev-parse", "HEAD")).stdout.decode().strip()
    registry = InMemoryWorktreeRegistry()
    data_dir = tmp_path / "app-data"
    manager = GitReviewWorktreeManager(
        data_dir=data_dir,
        git=git,
        registry=registry,
        locks=RepositoryLockRegistry(),
    )
    worktree = await manager.create("review-tampered", git_repository, head_oid)
    await asyncio.to_thread(_tamper_marker, worktree.root.parent / "ownership.json")

    with pytest.raises(WorktreeOwnershipError, match="marker mismatch"):
        await manager.verify_ownership(worktree)

    assert not await _path_exists(worktree.root)
    quarantine = data_dir / "quarantine"
    quarantined = await asyncio.to_thread(lambda: tuple(quarantine.iterdir()))
    assert len(quarantined) == 1


async def test_recovery_recreates_active_missing_checkout_and_removes_only_owned_orphan(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    git = RecordingGitCli()
    head_oid = (await git.run(git_repository, "rev-parse", "HEAD")).stdout.decode().strip()
    data_dir = tmp_path / "app-data"
    registry = InMemoryWorktreeRegistry()
    manager = GitReviewWorktreeManager(
        data_dir=data_dir,
        git=git,
        registry=registry,
        locks=RepositoryLockRegistry(),
    )
    artifacts = FilesystemInputArtifactStore(data_dir / "artifacts")
    lifecycle = ReviewWorktreeLifecycle(
        worktrees=manager,
        artifacts=artifacts,
        materializer=GitOverlayMaterializer(git),
    )
    captured = CapturedReviewInput(ReviewTarget(head_oid, head_oid, None), None)
    active = await lifecycle.create("review-active", git_repository, captured)
    orphan = await lifecycle.create("review-orphan", git_repository, captured)
    user_worktree = tmp_path / "user-recovery-worktree"
    await git.run(
        git_repository,
        "worktree",
        "add",
        "--detach",
        str(user_worktree),
        head_oid,
    )
    await git.run(
        git_repository,
        "worktree",
        "remove",
        "--force",
        str(active.root),
    )

    recovered = await ReviewWorktreeRecoveryService(
        lifecycle=lifecycle,
        registry=registry,
        recovery=manager,
    ).reconcile(
        {
            "review-active": WorktreeRecoveryInput(
                repository=git_repository,
                captured=captured,
            )
        }
    )

    assert recovered["review-active"].head_oid == head_oid
    assert await _path_exists(recovered["review-active"].root)
    assert not await _path_exists(orphan.root)
    assert await _path_exists(user_worktree)
    assert set(registry.records) == {"review-active"}
    assert not any(call[:2] == ("worktree", "prune") for call in git.calls)


async def test_recovery_drops_stale_terminal_missing_checkout() -> None:
    registry = InMemoryWorktreeRegistry()

    class Recovery:
        async def is_present(self, _worktree: TaskWorktree) -> bool:
            return False

        async def verify_ownership(self, _worktree: TaskWorktree) -> None:
            raise AssertionError("verify_ownership should not be called for stale terminal rows")

        async def forget_missing(self, _worktree: TaskWorktree, _repository: Path) -> None:
            raise AssertionError("forget_missing should not be called for stale terminal rows")

    class Lifecycle:
        def __init__(self) -> None:
            self.removed: list[str] = []

        async def remove_owned(self, worktree: TaskWorktree) -> None:
            self.removed.append(worktree.task_id)

        async def create(
            self,
            _task_id: str,
            _repository: Path,
            _captured: CapturedReviewInput,
        ) -> TaskWorktree:
            raise AssertionError("create should not be called for stale terminal rows")

    worktree = TaskWorktree(
        "worktree-terminal",
        "review-terminal",
        "d" * 64,
        Path("/tmp/nonexistent-terminal-worktree"),
        "e" * 40,
        "f" * 64,
    )
    await registry.register(worktree)

    lifecycle = Lifecycle()
    recovered = await ReviewWorktreeRecoveryService(
        lifecycle=lifecycle, registry=registry, recovery=Recovery()
    ).reconcile({})

    assert recovered == {}
    assert registry.records == {}
    assert lifecycle.removed == []

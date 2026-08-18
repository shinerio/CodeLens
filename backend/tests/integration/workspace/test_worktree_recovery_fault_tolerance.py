"""Verify that corrupted Git metadata during worktree recovery never crashes the process.

These tests cover three defence layers that prevent a single review task's
worktree failure from killing the entire backend:

1. ``GitReviewWorktreeManager.verify_ownership`` / ``forget_missing`` convert
   ``InvalidRepositoryError`` from ``_common_dir`` into ``WorktreeOwnershipError``
   and quarantine the worktree.
2. ``ReviewWorktreeRecoveryService.reconcile`` catches ``DomainError`` as a
   fallback so any domain-level error during reconciliation cleans the registry
   instead of propagating.
3. ``ReviewScheduler.run`` isolates ``recover`` failures so the poll loop
   continues and new reviews can still be claimed.
"""

import asyncio
from pathlib import Path

import pytest

from codelens.shared.domain.errors import InvalidRepositoryError, WorktreeOwnershipError
from codelens.workspace.application.worktree_lifecycle import (
    ReviewWorktreeRecoveryService,
    WorktreeRecoveryInput,
)
from codelens.workspace.domain.models import CapturedReviewInput, ReviewTarget, TaskWorktree
from codelens.workspace.infrastructure.git_cli import CommandResult, GitCli
from codelens.workspace.infrastructure.git_worktrees import (
    GitReviewWorktreeManager,
    RepositoryLockRegistry,
)


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


async def _path_exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


def _make_git_repository(tmp_path: Path) -> Path:
    """Create a real Git repository without relying on the ``-b`` flag."""

    import subprocess

    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, timeout=10)
    subprocess.run(
        ["git", "-C", str(repository), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "commit.gpgSign", "false"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "core.autocrlf", "false"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    (repository / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "README.md"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return repository


# ---------------------------------------------------------------------------
# Layer 1: verify_ownership / forget_missing convert InvalidRepositoryError
# ---------------------------------------------------------------------------


class _CorruptedCommonDirGitCli(GitCli):
    """GitCli that raises InvalidRepositoryError for ``rev-parse --git-common-dir``.

    All other commands delegate to the real GitCli, so the worktree can be
    created normally.  Only the ownership-verification path is corrupted.
    """

    def __init__(self) -> None:
        super().__init__()
        self._corrupted: set[Path] = set()

    def corrupt(self, path: Path) -> None:
        self._corrupted.add(path)

    async def run(
        self,
        repository: Path,
        *args: str,
        stdin: bytes | None = None,
        ok_codes: tuple[int, ...] = (0,),
    ) -> CommandResult:
        if (
            len(args) >= 2
            and args[0] == "rev-parse"
            and args[1] == "--git-common-dir"
            and repository in self._corrupted
        ):
            raise InvalidRepositoryError(
                "fatal: not a git repository (simulated corruption)"
            )
        return await super().run(repository, *args, stdin=stdin, ok_codes=ok_codes)


async def test_verify_ownership_converts_invalid_repository_error_to_worktree_ownership(
    tmp_path: Path,
) -> None:
    """verify_ownership converts InvalidRepositoryError to WorktreeOwnershipError."""

    git_repository = await asyncio.to_thread(_make_git_repository, tmp_path)
    git = _CorruptedCommonDirGitCli()
    head_oid = (await git.run(git_repository, "rev-parse", "HEAD")).stdout.decode().strip()
    registry = InMemoryWorktreeRegistry()
    data_dir = tmp_path / "app-data"
    manager = GitReviewWorktreeManager(
        data_dir=data_dir,
        git=git,
        registry=registry,
        locks=RepositoryLockRegistry(),
    )
    worktree = await manager.create("review-corrupted", git_repository, head_oid)
    git.corrupt(worktree.root)

    with pytest.raises(WorktreeOwnershipError, match="metadata is unreadable"):
        await manager.verify_ownership(worktree)

    assert not await _path_exists(worktree.root)
    quarantine = data_dir / "quarantine"
    quarantined = await asyncio.to_thread(lambda: tuple(quarantine.iterdir()))
    assert len(quarantined) == 1


async def test_forget_missing_converts_invalid_repository_error_to_worktree_ownership(
    tmp_path: Path,
) -> None:
    """forget_missing converts InvalidRepositoryError to WorktreeOwnershipError."""

    git_repository = await asyncio.to_thread(_make_git_repository, tmp_path)
    git = _CorruptedCommonDirGitCli()
    head_oid = (await git.run(git_repository, "rev-parse", "HEAD")).stdout.decode().strip()
    registry = InMemoryWorktreeRegistry()
    data_dir = tmp_path / "app-data"
    manager = GitReviewWorktreeManager(
        data_dir=data_dir,
        git=git,
        registry=registry,
        locks=RepositoryLockRegistry(),
    )
    worktree = await manager.create("review-forget-corrupted", git_repository, head_oid)
    # Simulate the checkout being deleted so forget_missing is the recovery path
    await asyncio.to_thread(
        lambda: __import__("shutil").rmtree(worktree.root, ignore_errors=True)
    )
    git.corrupt(git_repository)

    with pytest.raises(WorktreeOwnershipError, match="metadata is unreadable"):
        await manager.forget_missing(worktree, git_repository)


# ---------------------------------------------------------------------------
# Layer 2: reconcile catches DomainError and cleans the registry
# ---------------------------------------------------------------------------


async def test_reconcile_cleans_registry_when_remove_owned_raises_domain_error() -> None:
    """reconcile must not propagate DomainError when removing an orphan worktree."""

    worktree = TaskWorktree(
        "worktree-orphan",
        "review-orphan",
        "d" * 64,
        Path("/tmp/nonexistent-orphan-worktree"),
        "e" * 40,
        "f" * 64,
    )
    registry = InMemoryWorktreeRegistry()
    await registry.register(worktree)

    class FailingRecovery:
        async def is_present(self, _worktree: TaskWorktree) -> bool:
            return True

        async def verify_ownership(self, _worktree: TaskWorktree) -> None:
            raise AssertionError("should not reach verify_ownership for orphan")

        async def forget_missing(self, _worktree: TaskWorktree, _repository: Path) -> None:
            raise AssertionError("should not reach forget_missing for orphan")

    class FailingLifecycle:
        async def remove_owned(self, _worktree: TaskWorktree) -> None:
            raise InvalidRepositoryError("simulated git corruption during remove_owned")

        async def create(
            self,
            _task_id: str,
            _repository: Path,
            _captured: CapturedReviewInput,
        ) -> TaskWorktree:
            raise AssertionError("create should not be called for orphan")

    recovered = await ReviewWorktreeRecoveryService(
        lifecycle=FailingLifecycle(),
        registry=registry,
        recovery=FailingRecovery(),
    ).reconcile({})

    assert recovered == {}
    assert registry.records == {}


async def test_reconcile_does_not_propagate_domain_error_from_verify_ownership(
    tmp_path: Path,
) -> None:
    """reconcile must not propagate DomainError when verify_ownership fails.

    When Git metadata is corrupted, verify_ownership raises WorktreeOwnershipError
    (a DomainError subclass).  reconcile must catch it, clean the registry, and
    attempt recreation rather than letting the error propagate to recover().
    """

    class FailingRecovery:
        async def is_present(self, _worktree: TaskWorktree) -> bool:
            return True

        async def verify_ownership(self, _worktree: TaskWorktree) -> None:
            raise WorktreeOwnershipError("simulated ownership failure")

        async def forget_missing(self, _worktree: TaskWorktree, _repository: Path) -> None:
            raise AssertionError("forget_missing should not be called when present")

    class RecreatingLifecycle:
        def __init__(self) -> None:
            self.recreated: list[str] = []

        async def remove_owned(self, _worktree: TaskWorktree) -> None:
            raise AssertionError("remove_owned should not be called for active task")

        async def create(
            self,
            task_id: str,
            _repository: Path,
            _captured: CapturedReviewInput,
        ) -> TaskWorktree:
            self.recreated.append(task_id)
            return TaskWorktree(
                "worktree-recreated",
                task_id,
                "d" * 64,
                Path("/tmp/recreated-worktree"),
                "e" * 40,
                "f" * 64,
            )

    worktree = TaskWorktree(
        "worktree-original",
        "review-active-corrupted",
        "d" * 64,
        Path("/tmp/original-worktree"),
        "e" * 40,
        "f" * 64,
    )
    registry = InMemoryWorktreeRegistry()
    await registry.register(worktree)
    captured = CapturedReviewInput(
        ReviewTarget("a" * 40, "b" * 40, None), None
    )
    lifecycle = RecreatingLifecycle()

    recovered = await ReviewWorktreeRecoveryService(
        lifecycle=lifecycle,
        registry=registry,
        recovery=FailingRecovery(),
    ).reconcile(
        {
            "review-active-corrupted": WorktreeRecoveryInput(
                repository=Path("/tmp/repo"),
                captured=captured,
            )
        }
    )

    assert "review-active-corrupted" in recovered
    assert lifecycle.recreated == ["review-active-corrupted"]
    assert recovered["review-active-corrupted"].worktree_id == "worktree-recreated"


# ---------------------------------------------------------------------------
# Layer 3: scheduler.run isolates recover failures
# ---------------------------------------------------------------------------


async def test_scheduler_continues_polling_after_recovery_failure(tmp_path: Path) -> None:
    """scheduler.run must not exit when recover raises; the poll loop must continue."""

    from codelens.worker.scheduler import ReviewScheduler, WorkerSemaphores

    class FakeSingleton:
        async def acquire(self) -> None:
            pass

        async def release(self) -> None:
            pass

    class FakeJob:
        def __init__(self, task_id: str) -> None:
            self._task_id = task_id

        @property
        def task_id(self) -> str:
            return self._task_id

    class FakeQueue:
        def __init__(self) -> None:
            self._call_count = 0
            self._max_calls = 3

        async def next_queued(self) -> FakeJob | None:
            self._call_count += 1
            if self._call_count > self._max_calls:
                return None
            return FakeJob("fake-job")

    executed_tasks: list[str] = []
    recovery_failed = asyncio.Event()

    async def fake_execute(task_id: str) -> None:
        executed_tasks.append(task_id)

    async def failing_recover() -> None:
        recovery_failed.set()
        raise RuntimeError("simulated recovery crash")

    async def noop_close() -> None:
        pass

    async def record_failure(task_id: str, error: Exception) -> None:
        pass

    async def record_claim(task_id: str) -> None:
        pass

    stop = asyncio.Event()
    scheduler = ReviewScheduler(
        queue=FakeQueue(),  # type: ignore[arg-type]
        execute=fake_execute,
        singleton=FakeSingleton(),  # type: ignore[arg-type]
        recover=failing_recover,
        close=noop_close,
        semaphores=WorkerSemaphores.create(
            agent_limit=1, model_limit=1, tool_limit=1
        ),
        max_active_reviews=1,
        poll_min_seconds=0.01,
        poll_max_seconds=0.05,
        record_failure=record_failure,
        record_claim=record_claim,
    )

    # Run scheduler briefly, then stop it
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.wait_for(recovery_failed.wait(), timeout=2.0)

    # Give the poll loop a moment to claim at least one job
    await asyncio.sleep(0.2)
    stop.set()

    try:
        await asyncio.wait_for(task, timeout=3.0)
    except TimeoutError:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pytest.fail("scheduler did not stop in time")

    assert recovery_failed.is_set(), "recovery should have failed"
    assert len(executed_tasks) > 0, "scheduler should have continued polling after recovery failure"

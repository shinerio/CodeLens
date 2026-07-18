import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codelens.bootstrap.settings import Settings
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import UnvalidatedAgentOutput
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.repositories import SqlReviewStore
from codelens.worker.main import build_worker
from codelens.worker.scheduler import ReviewScheduler, WorkerSemaphores
from codelens.workspace.domain.models import BranchScope, ReviewTarget
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.repository_metadata import GitRepositoryMetadataAdapter


@dataclass(frozen=True)
class Job:
    task_id: str


class Queue:
    def __init__(self) -> None:
        self.jobs = [Job("review-1"), Job("review-2")]

    async def next_queued(self) -> Job | None:
        return self.jobs.pop(0) if self.jobs else None


class Singleton:
    def __init__(self) -> None:
        self.acquired = False
        self.released = False

    async def acquire(self) -> None:
        self.acquired = True

    async def release(self) -> None:
        self.released = True


async def test_scheduler_runs_distinct_tasks_concurrently_and_releases_after_close() -> None:
    singleton = Singleton()
    both_running = asyncio.Event()
    release = asyncio.Event()
    stop = asyncio.Event()
    active: set[str] = set()
    maximum = 0
    lifecycle: list[str] = []

    async def execute(task_id: str) -> None:
        nonlocal maximum
        active.add(task_id)
        maximum = max(maximum, len(active))
        if len(active) == 2:
            both_running.set()
        await release.wait()
        active.remove(task_id)
        if not active:
            stop.set()

    async def recover() -> None:
        lifecycle.append("recover")

    async def close() -> None:
        lifecycle.append("close")
        assert not active

    scheduler = ReviewScheduler(
        queue=Queue(),
        execute=execute,
        singleton=singleton,
        recover=recover,
        close=close,
        semaphores=WorkerSemaphores.create(agent_limit=2, model_limit=2, tool_limit=2),
        max_active_reviews=2,
        poll_min_seconds=0.001,
        poll_max_seconds=0.01,
    )
    running = asyncio.create_task(scheduler.run(stop))

    await asyncio.wait_for(both_running.wait(), timeout=1)
    release.set()
    await asyncio.wait_for(running, timeout=1)

    assert maximum == 2
    assert singleton.acquired and singleton.released
    assert lifecycle == ["recover", "close"]


async def test_shutdown_cancels_active_reviews_and_failure_isolated() -> None:
    stop = asyncio.Event()
    canceled = asyncio.Event()
    failures: list[str] = []

    async def execute(task_id: str) -> None:
        if task_id == "review-1":
            raise ValueError("one task failed")
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    async def record_failure(task_id: str, _error_code: str) -> None:
        failures.append(task_id)
        stop.set()

    scheduler = ReviewScheduler(
        queue=Queue(),
        execute=execute,
        singleton=Singleton(),
        recover=lambda: _noop(),
        close=lambda: _noop(),
        record_failure=record_failure,
        semaphores=WorkerSemaphores.create(agent_limit=1, model_limit=1, tool_limit=1),
        max_active_reviews=2,
        poll_min_seconds=0.001,
        poll_max_seconds=0.01,
    )

    await asyncio.wait_for(scheduler.run(stop), timeout=1)

    assert failures == ["review-1"]
    assert canceled.is_set()


async def _noop() -> None:
    return None


class GatedRuntime:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.maximum = 0
        self.calls = 0

    async def invoke(self, _agent: object, _payload: bytes) -> UnvalidatedAgentOutput:
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        if self.active == 2:
            self.entered.set()
        try:
            await self.release.wait()
            return UnvalidatedAgentOutput(
                b'{"schema_version":"1","findings":[]}', (), "fake", 0, 0, ()
            )
        finally:
            self.active -= 1


async def test_two_refs_in_one_real_repository_review_in_distinct_worktrees(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    git = GitCli()
    base_oid = (await git.run(git_repository, "rev-parse", "HEAD")).stdout.decode().strip()
    heads: list[tuple[str, str, str]] = []
    for branch, source_path in (("feature-one", "one.py"), ("feature-two", "two.py")):
        await git.run(git_repository, "checkout", "-b", branch)
        await asyncio.to_thread(
            (git_repository / source_path).write_text,
            f"BRANCH = {branch!r}\n",
            encoding="utf-8",
        )
        await git.run(git_repository, "add", source_path)
        await git.run(git_repository, "commit", "-m", branch)
        head_oid = (await git.run(git_repository, "rev-parse", "HEAD")).stdout.decode().strip()
        heads.append((branch, source_path, head_oid))
        await git.run(git_repository, "checkout", "main")

    data_dir = tmp_path / "worker-data"
    settings = Settings(
        data_dir=data_dir,
        repository_roots=(git_repository,),
        database_url=f"sqlite+aiosqlite:///{data_dir / 'review.sqlite3'}",
        max_active_reviews=2,
        max_active_agent_runs=2,
        max_agent_runs_per_review=1,
    )
    await asyncio.to_thread(data_dir.mkdir, parents=True)
    database = Database(settings.resolved_database_url)
    await database.migrate()
    repository = await asyncio.to_thread(git_repository.resolve)
    metadata = await GitRepositoryMetadataAdapter(git).inspect(repository)
    store = SqlReviewStore(database)
    for index, (branch, source_path, head_oid) in enumerate(heads, start=1):
        await store.create_with_job(
            ReviewTask.create(
                task_id=f"review-{index}",
                repository_id=metadata.repository_id,
                repository_realpath_hash=metadata.repository_realpath_hash,
                git_common_dir_hash=metadata.git_common_dir_hash,
                repository_path=git_repository,
                target_paths=(source_path,),
                scope=BranchScope(base_ref="main", target_ref=branch),
                target=ReviewTarget(base_oid, head_oid, None),
                selected_agent_versions=("correctness:v1",),
                created_at=datetime.now(UTC),
            )
        )
    await database.dispose()

    runtime = GatedRuntime()
    worker = build_worker(settings, runtime=runtime)
    stop = asyncio.Event()
    running = asyncio.create_task(worker.run(stop))
    try:
        await asyncio.wait_for(runtime.entered.wait(), timeout=5)
        worktrees = await worker.worktree_registry.list_all()
        assert len(worktrees) == 2
        assert len({item.root for item in worktrees}) == 2
        assert runtime.maximum == 2

        runtime.release.set()
        for _attempt in range(100):
            statuses = [
                (await worker.review_store.get_review(f"review-{index}")).status  # type: ignore[union-attr]
                for index in (1, 2)
            ]
            if statuses == ["completed", "completed"]:
                break
            await asyncio.sleep(0.01)
        assert statuses == ["completed", "completed"]
        for _attempt in range(100):
            if not await worker.worktree_registry.list_all():
                break
            await asyncio.sleep(0.01)
        assert await worker.worktree_registry.list_all() == ()
    finally:
        runtime.release.set()
        stop.set()
        await asyncio.wait_for(running, timeout=5)

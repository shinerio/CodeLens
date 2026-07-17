import asyncio
from pathlib import Path

import pytest

from codelens.instruction_policy.application.resolver import InstructionResolver
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser
from codelens.instruction_policy.infrastructure.structured_skip import StructuredSkipMatcher
from codelens.shared.domain.errors import SnapshotStaleError, WorktreeMutatedError
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.create_snapshot import SnapshotService
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.application.worktree_lifecycle import ReviewWorktreeLifecycle
from codelens.workspace.domain.models import (
    BranchScope,
    RepositoryFingerprint,
    TaskWorktree,
    UncommittedScope,
)
from codelens.workspace.infrastructure.change_index import GitChangeIndexBuilder
from codelens.workspace.infrastructure.filesystem_snapshot import FilesystemSnapshotBuilder
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_ignore import GitIgnoreResolver
from codelens.workspace.infrastructure.git_overlay import (
    GitOverlayMaterializer,
    GitReviewInputCaptureAdapter,
)
from codelens.workspace.infrastructure.git_workspace import GitWorkspaceAdapter
from codelens.workspace.infrastructure.git_worktrees import (
    GitReviewWorktreeManager,
    RepositoryLockRegistry,
)
from codelens.workspace.infrastructure.input_artifacts import FilesystemInputArtifactStore


class SnapshotWorktreeRegistry:
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


class AlwaysMutatingCaptureSource:
    def __init__(self, source: GitReviewInputCaptureAdapter, repository: Path) -> None:
        self._source = source
        self._repository = repository
        self._mutation = 0

    async def fingerprint(
        self,
        repository: Path,
        target_paths: tuple[str, ...],
    ) -> RepositoryFingerprint:
        return await self._source.fingerprint(repository, target_paths)

    async def capture_overlay(self, repository: Path, target_paths: tuple[str, ...]) -> bytes:
        payload = await self._source.capture_overlay(repository, target_paths)
        self._mutation += 1
        (self._repository / "README.md").write_text(
            f"# mutation {self._mutation}\n",
            encoding="utf-8",
        )
        return payload


async def _read_text(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def _path_exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


async def _artifact_files(root: Path) -> tuple[Path, ...]:
    def list_files() -> tuple[Path, ...]:
        return tuple(path for path in root.rglob("*") if path.is_file()) if root.exists() else ()

    return await asyncio.to_thread(list_files)


async def test_captures_overlay_and_ignored_control_inputs_before_source_changes(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    git = GitCli()
    (git_repository / ".gitignore").write_text(
        "ignored.tmp\nREVIEW.md\n*.review.md\n",
        encoding="utf-8",
    )
    (git_repository / "README.md").write_text("# captured\n", encoding="utf-8")
    (git_repository / "allowed.py").write_text("VALUE = 'captured'\n", encoding="utf-8")
    (git_repository / "ignored.tmp").write_text("do not capture\n", encoding="utf-8")
    (git_repository / "REVIEW.md").write_text("Root captured rule\n", encoding="utf-8")
    (git_repository / "allowed.py.review.md").write_text(
        "File captured rule\n",
        encoding="utf-8",
    )
    scope_plan = await ScopePlanner(GitWorkspaceAdapter(git)).plan(
        git_repository,
        UncommittedScope(),
    )
    artifact_root = tmp_path / "app-data" / "artifacts" / "inputs"
    artifacts = FilesystemInputArtifactStore(artifact_root)
    captured = await ReviewInputCaptureService(
        GitReviewInputCaptureAdapter(git),
        artifacts,
    ).capture(git_repository, scope_plan)
    (git_repository / "README.md").write_text("# later edit\n", encoding="utf-8")
    registry = SnapshotWorktreeRegistry()
    manager = GitReviewWorktreeManager(
        data_dir=tmp_path / "app-data",
        git=git,
        registry=registry,
        locks=RepositoryLockRegistry(),
    )
    lifecycle = ReviewWorktreeLifecycle(
        worktrees=manager,
        artifacts=artifacts,
        materializer=GitOverlayMaterializer(git),
    )

    worktree = await lifecycle.create("review-overlay", git_repository, captured)

    assert await _read_text(worktree.root / "README.md") == "# captured\n"
    assert await _read_text(worktree.root / "allowed.py") == "VALUE = 'captured'\n"
    assert not await _path_exists(worktree.root / "ignored.tmp")
    assert await _read_text(worktree.root / "REVIEW.md") == "Root captured rule\n"
    assert await _read_text(worktree.root / "allowed.py.review.md") == "File captured rule\n"


async def test_capture_retries_once_then_fails_when_source_keeps_changing(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    git = GitCli()
    (git_repository / "README.md").write_text("# initial dirty state\n", encoding="utf-8")
    scope_plan = await ScopePlanner(GitWorkspaceAdapter(git)).plan(
        git_repository,
        UncommittedScope(),
    )
    artifact_root = tmp_path / "artifacts"
    artifacts = FilesystemInputArtifactStore(artifact_root)
    source = AlwaysMutatingCaptureSource(
        GitReviewInputCaptureAdapter(git),
        git_repository,
    )

    with pytest.raises(SnapshotStaleError):
        await ReviewInputCaptureService(source, artifacts).capture(git_repository, scope_plan)

    assert await _artifact_files(artifact_root) == ()


async def test_freezes_manifest_change_index_and_detects_reviewer_mutation(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    git = GitCli()
    (git_repository / "tracked.log").write_text("tracked but ignored\n", encoding="utf-8")
    (git_repository / "REVIEW.md").write_text("Check state transitions.\n", encoding="utf-8")
    state_file = git_repository / "src" / "state.py"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("def ready(value: bool) -> bool:\n    return value\n", encoding="utf-8")
    await git.run(git_repository, "add", "tracked.log", "REVIEW.md", "src/state.py")
    await git.run(git_repository, "commit", "-m", "add review fixture")
    (git_repository / ".gitignore").write_text("*.log\n", encoding="utf-8")
    await git.run(git_repository, "add", ".gitignore")
    await git.run(git_repository, "commit", "-m", "ignore logs")
    await git.run(git_repository, "checkout", "-b", "feature-state")
    state_file.write_text(
        "def ready(value: bool) -> bool:\n    return not value\n",
        encoding="utf-8",
    )
    await git.run(git_repository, "add", "src/state.py")
    await git.run(git_repository, "commit", "-m", "invert state guard")
    await git.run(git_repository, "checkout", "main")
    scope_plan = await ScopePlanner(GitWorkspaceAdapter(git)).plan(
        git_repository,
        BranchScope(base_ref="main", target_ref="feature-state"),
    )
    artifact_store = FilesystemInputArtifactStore(tmp_path / "app-data" / "artifacts")
    captured = await ReviewInputCaptureService(
        GitReviewInputCaptureAdapter(git),
        artifact_store,
    ).capture(git_repository, scope_plan)
    registry = SnapshotWorktreeRegistry()
    manager = GitReviewWorktreeManager(
        data_dir=tmp_path / "app-data",
        git=git,
        registry=registry,
        locks=RepositoryLockRegistry(),
    )
    lifecycle = ReviewWorktreeLifecycle(
        worktrees=manager,
        artifacts=artifact_store,
        materializer=GitOverlayMaterializer(git),
    )
    manifest_builder = FilesystemSnapshotBuilder(
        git=git,
        ignore=GitIgnoreResolver(git),
    )
    snapshot = await SnapshotService(
        lifecycle=lifecycle,
        manifest_builder=manifest_builder,
        change_index=GitChangeIndexBuilder(git),
        artifacts=artifact_store,
        instructions=InstructionResolver(MarkdownInstructionParser()),
        structured_skip=StructuredSkipMatcher(),
    ).create("review-snapshot", git_repository, captured, scope_plan)

    assert snapshot.manifest.target_paths == ("src/state.py",)
    assert snapshot.manifest.instruction_paths == ("REVIEW.md",)
    assert "tracked.log" not in snapshot.manifest.context_paths
    assert any(item.path == "tracked.log" for item in snapshot.manifest.excluded_paths)
    assert snapshot.change_index.contains("src/state.py", 2, 2, "new")
    assert snapshot.snapshot_artifact is not None

    await asyncio.to_thread(
        (snapshot.worktree.root / "src" / "state.py").write_text,
        "MUTATED = True\n",
        encoding="utf-8",
    )
    with pytest.raises(WorktreeMutatedError):
        await manifest_builder.verify(snapshot)

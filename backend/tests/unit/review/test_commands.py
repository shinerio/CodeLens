from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from codelens.findings.domain.existing_findings import ExistingFinding
from codelens.review.application.commands import (
    CreateReviewCommand,
    CreateReviewHandler,
    DeleteReviewHandler,
)
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import ReviewRecord
from codelens.review.domain.review_strategy import (
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.shared.domain.errors import SnapshotStaleError
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.domain.models import (
    BranchScope,
    OpaqueArtifact,
    RepositoryFingerprint,
    TaskWorktree,
)
from codelens.workspace.domain.ports import RepositoryInfo, ScopePlan
from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy


class FixedPlanner:
    async def plan_scope(self, _repository: Path, _scope: object) -> ScopePlan:
        return ScopePlan("a" * 40, "b" * 40, ("src/app.py",), True, "branch")


class StableCaptureSource:
    async def fingerprint(
        self,
        _repository: Path,
        _candidate_paths: tuple[str, ...],
    ) -> RepositoryFingerprint:
        return RepositoryFingerprint("b" * 40, "c" * 64, "d" * 64)

    async def capture_overlay(
        self,
        _repository: Path,
        _candidate_paths: tuple[str, ...],
    ) -> bytes:
        return b"captured"


class MutatingCaptureSource(StableCaptureSource):
    def __init__(self) -> None:
        self._version = 0

    async def fingerprint(
        self,
        _repository: Path,
        _candidate_paths: tuple[str, ...],
    ) -> RepositoryFingerprint:
        self._version += 1
        return RepositoryFingerprint("b" * 40, "c" * 64, f"{self._version:064x}")


class RecordingArtifacts:
    def __init__(self) -> None:
        self.discarded: list[str] = []
        self._version = 0

    async def write_bytes(self, _payload: bytes) -> OpaqueArtifact:
        self._version += 1
        return OpaqueArtifact(
            f"input_{self._version:032x}.json",
            f"{self._version:064x}",
            8,
        )

    async def read_bytes(self, _reference: str, _expected_hash: str) -> bytes:
        return b"captured"

    async def discard(self, reference: str) -> None:
        self.discarded.append(reference)


class FailingStore:
    def __init__(self, *, fail_create: bool, duplicate: object | None = None) -> None:
        self.fail_create = fail_create
        self.duplicate = duplicate
        self.created: list[ReviewTask] = []

    async def create_with_job(self, task: ReviewTask) -> None:
        self.created.append(task)
        if self.fail_create:
            raise RuntimeError("database unavailable")

    async def get_review(self, _task_id: str) -> ReviewRecord | None:
        return None

    async def request_cancellation(self, _task_id: str) -> ReviewRecord | None:
        return None

    async def find_duplicate_review(
        self, *, repository_id: str, base_oid: str, head_oid: str
    ) -> object | None:
        return self.duplicate


class TriggeredStore:
    """Store that tracks triggered reviews for supersede testing."""

    def __init__(self) -> None:
        self.triggered: list[ReviewTask] = []
        self.manual: list[ReviewTask] = []

    async def create_triggered_with_job(
        self, task: ReviewTask
    ) -> tuple[ReviewRecord | None, bool]:
        self.triggered.append(task)
        return SimpleNamespace(task_id=task.task_id), True

    async def create_with_job(self, task: ReviewTask) -> None:
        self.manual.append(task)

    async def get_review(self, _task_id: str) -> ReviewRecord | None:
        return SimpleNamespace(task_id=_task_id)


class EnabledIdempotencySettings:
    async def get(self) -> object:
        return SimpleNamespace(enabled=True)


class ConfiguredFileExclusionPolicy:
    async def get(self) -> ReviewFileExclusionPolicy:
        return ReviewFileExclusionPolicy(
            suffixes=(".log",),
            path_regexes=(r"(?:^|/)generated(?:/|$)",),
        )


class FailingExistingFindingsProvider:
    async def load(self, _repository_path: Path) -> tuple[ExistingFinding, ...]:
        raise ValueError("historical report is invalid")


class DeletingStore:
    async def soft_delete_review(self, _task_id: str) -> bool:
        return True


class RecordingWorktreeRegistry:
    def __init__(self, worktree: TaskWorktree) -> None:
        self._worktree = worktree

    async def get(self, _task_id: str) -> TaskWorktree | None:
        return self._worktree


class RecordingWorktreeManager:
    def __init__(self) -> None:
        self.removed: list[TaskWorktree] = []

    async def remove_owned(self, worktree: TaskWorktree) -> None:
        self.removed.append(worktree)


def _command(tmp_path: Path) -> CreateReviewCommand:
    repository = RepositoryInfo(
        path=tmp_path,
        repository_id="repository_" + "a" * 64,
        repository_realpath_hash="b" * 64,
        git_common_dir_hash="c" * 64,
        head_sha="d" * 40,
        current_branch="main",
        is_dirty=True,
    )
    return CreateReviewCommand(
        repository=repository,
        scope=BranchScope("main", "HEAD", True),
        review_profile=ReviewProfileSnapshot(FixedReviewerSelection(("correctness:v2",))),
    )


async def test_create_failure_discards_the_just_captured_overlay(tmp_path: Path) -> None:
    artifacts = RecordingArtifacts()
    store = FailingStore(fail_create=True)
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,
        artifacts,
        id_factory=lambda: "review_" + "1" * 32,
        clock=lambda: datetime(2026, 7, 17, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await handler.handle(_command(tmp_path))

    assert len(store.created) == 1
    assert artifacts.discarded == ["input_00000000000000000000000000000001.json"]


async def test_existing_findings_load_failure_discards_captured_overlay(tmp_path: Path) -> None:
    artifacts = RecordingArtifacts()
    store = FailingStore(fail_create=False)
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,
        artifacts,
        existing_findings_provider=FailingExistingFindingsProvider(),
    )

    with pytest.raises(ValueError, match="historical report is invalid"):
        await handler.handle(_command(tmp_path))

    assert store.created == []
    assert artifacts.discarded == ["input_00000000000000000000000000000001.json"]


async def test_create_freezes_the_current_configured_file_exclusion_policy(
    tmp_path: Path,
) -> None:
    artifacts = RecordingArtifacts()
    store = FailingStore(fail_create=False)
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,
        artifacts,
        file_exclusion_settings=ConfiguredFileExclusionPolicy(),
    )

    with pytest.raises(RuntimeError, match="could not be reloaded"):
        await handler.handle(_command(tmp_path))

    assert len(store.created) == 1
    frozen_policy = ReviewFileExclusionPolicy.from_json(
        store.created[0].file_exclusion_policy_json
    )
    assert frozen_policy.suffixes == (".log",)
    assert frozen_policy.path_regexes == (r"(?:^|/)generated(?:/|$)",)


async def test_stale_capture_never_creates_a_durable_command(tmp_path: Path) -> None:
    artifacts = RecordingArtifacts()
    store = FailingStore(fail_create=False)
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(MutatingCaptureSource(), artifacts),
        store,
        artifacts,
    )

    with pytest.raises(SnapshotStaleError):
        await handler.handle(_command(tmp_path))

    assert store.created == []
    assert artifacts.discarded == [
        "input_00000000000000000000000000000001.json",
        "input_00000000000000000000000000000002.json",
    ]


async def test_legacy_plugin_duplicate_keeps_existing_review_and_discards_overlay(
    tmp_path: Path,
) -> None:
    artifacts = RecordingArtifacts()
    existing = SimpleNamespace(task_id="review-existing")
    store = FailingStore(fail_create=False, duplicate=existing)
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,  # type: ignore[arg-type]
        artifacts,
        idempotency_settings=EnabledIdempotencySettings(),  # type: ignore[arg-type]
    )
    command = _command(tmp_path)
    command = CreateReviewCommand(
        repository=command.repository,
        scope=command.scope,
        review_profile=command.review_profile,
        trigger_source="plugin",
        skip_if_duplicate=True,
    )

    result = await handler.handle(command)

    assert result is existing
    assert store.created == []
    assert artifacts.discarded == ["input_00000000000000000000000000000001.json"]


async def test_delete_review_removes_its_registered_owned_worktree(tmp_path: Path) -> None:
    task_id = "review_" + "1" * 32
    worktree = TaskWorktree(
        worktree_id="worktree-1",
        task_id=task_id,
        repository_common_dir_hash="a" * 64,
        root=tmp_path / "worktrees" / task_id / "checkout",
        head_oid="b" * 40,
        ownership_token_hash="c" * 64,
    )
    registry = RecordingWorktreeRegistry(worktree)
    manager = RecordingWorktreeManager()
    handler = DeleteReviewHandler(DeletingStore(), registry, manager)

    await handler.handle(task_id)

    assert manager.removed == [worktree]


async def test_supersede_policy_computes_trigger_keys(tmp_path: Path) -> None:
    """When supersede_policy is set, handler computes trigger_slot_key and idempotency_key."""
    artifacts = RecordingArtifacts()
    store = TriggeredStore()
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,
        artifacts,
        id_factory=lambda: "review_" + "1" * 32,
        clock=lambda: datetime(2026, 7, 17, tzinfo=UTC),
    )

    base_command = _command(tmp_path)
    command = CreateReviewCommand(
        repository=base_command.repository,
        scope=base_command.scope,
        review_profile=base_command.review_profile,
        trigger_source="plugin",
        supersede_policy="latest_snapshot",
        skip_if_duplicate=True,
    )

    await handler.handle(command)

    # Verify triggered path was used (not manual path)
    assert len(store.triggered) == 1
    assert len(store.manual) == 0

    task = store.triggered[0]
    # Verify keys were computed
    assert task.trigger_slot_key is not None
    assert task.idempotency_key is not None
    assert task.supersede_policy == "latest_snapshot"


async def test_same_repository_profile_share_slot_key(tmp_path: Path) -> None:
    """Same repository + profile + locale should produce the same trigger_slot_key."""
    artifacts = RecordingArtifacts()
    store = TriggeredStore()
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,
        artifacts,
        id_factory=lambda: f"review_{len(store.triggered):032x}",
        clock=lambda: datetime(2026, 7, 17, tzinfo=UTC),
    )

    base_command = _command(tmp_path)
    command = CreateReviewCommand(
        repository=base_command.repository,
        scope=base_command.scope,
        review_profile=base_command.review_profile,
        trigger_source="plugin",
        supersede_policy="latest_snapshot",
        skip_if_duplicate=True,
    )

    # Create two reviews with same repository + profile + locale
    await handler.handle(command)
    await handler.handle(command)

    assert len(store.triggered) == 2
    task1 = store.triggered[0]
    task2 = store.triggered[1]

    # Same slot key (same repository + profile + locale)
    assert task1.trigger_slot_key == task2.trigger_slot_key
    # Different idempotency keys (different task_id means different capture time)
    assert task1.idempotency_key != task2.idempotency_key


async def test_different_locale_different_slot_key(tmp_path: Path) -> None:
    """Different prompt_locale should produce different trigger_slot_key."""
    artifacts = RecordingArtifacts()
    store = TriggeredStore()
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,
        artifacts,
        id_factory=lambda: f"review_{len(store.triggered):032x}",
        clock=lambda: datetime(2026, 7, 17, tzinfo=UTC),
    )

    base_command = _command(tmp_path)

    # Create review with en locale
    command_en = CreateReviewCommand(
        repository=base_command.repository,
        scope=base_command.scope,
        review_profile=base_command.review_profile,
        trigger_source="plugin",
        supersede_policy="latest_snapshot",
        prompt_locale="en",
        skip_if_duplicate=True,
    )
    await handler.handle(command_en)

    # Create review with zh locale
    command_zh = CreateReviewCommand(
        repository=base_command.repository,
        scope=base_command.scope,
        review_profile=base_command.review_profile,
        trigger_source="plugin",
        supersede_policy="latest_snapshot",
        prompt_locale="zh",
        skip_if_duplicate=True,
    )
    await handler.handle(command_zh)

    assert len(store.triggered) == 2
    task_en = store.triggered[0]
    task_zh = store.triggered[1]

    # Different slot keys (different locale)
    assert task_en.trigger_slot_key != task_zh.trigger_slot_key


async def test_no_supersede_policy_uses_manual_path(tmp_path: Path) -> None:
    """When supersede_policy is None, handler uses manual create_with_job path."""
    artifacts = RecordingArtifacts()
    store = TriggeredStore()
    handler = CreateReviewHandler(
        ScopePlanner(FixedPlanner()),
        ReviewInputCaptureService(StableCaptureSource(), artifacts),
        store,
        artifacts,
        id_factory=lambda: "review_" + "1" * 32,
        clock=lambda: datetime(2026, 7, 17, tzinfo=UTC),
    )

    base_command = _command(tmp_path)
    command = CreateReviewCommand(
        repository=base_command.repository,
        scope=base_command.scope,
        review_profile=base_command.review_profile,
        trigger_source="manual",
        supersede_policy=None,  # No supersede
    )

    await handler.handle(command)

    # Verify manual path was used (not triggered path)
    assert len(store.manual) == 1
    assert len(store.triggered) == 0

    task = store.manual[0]
    # Verify keys are None for manual path
    assert task.trigger_slot_key is None
    assert task.idempotency_key is None

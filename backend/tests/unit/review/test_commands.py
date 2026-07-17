from datetime import UTC, datetime
from pathlib import Path

import pytest

from codelens.review.application.commands import CreateReviewCommand, CreateReviewHandler
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import ReviewRecord
from codelens.shared.domain.errors import SnapshotStaleError
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.domain.models import (
    BranchScope,
    OpaqueArtifact,
    RepositoryFingerprint,
)
from codelens.workspace.domain.ports import RepositoryInfo, ScopePlan


class FixedPlanner:
    async def plan_scope(self, _repository: Path, _scope: object) -> ScopePlan:
        return ScopePlan("a" * 40, "b" * 40, ("src/app.py",), True)


class StableCaptureSource:
    async def fingerprint(
        self,
        _repository: Path,
        _target_paths: tuple[str, ...],
    ) -> RepositoryFingerprint:
        return RepositoryFingerprint("b" * 40, "c" * 64, "d" * 64)

    async def capture_overlay(
        self,
        _repository: Path,
        _target_paths: tuple[str, ...],
    ) -> bytes:
        return b"captured"


class MutatingCaptureSource(StableCaptureSource):
    def __init__(self) -> None:
        self._version = 0

    async def fingerprint(
        self,
        _repository: Path,
        _target_paths: tuple[str, ...],
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
    def __init__(self, *, fail_create: bool) -> None:
        self.fail_create = fail_create
        self.created: list[ReviewTask] = []

    async def create_with_job(self, task: ReviewTask) -> None:
        self.created.append(task)
        if self.fail_create:
            raise RuntimeError("database unavailable")

    async def get_review(self, _task_id: str) -> ReviewRecord | None:
        return None

    async def request_cancellation(self, _task_id: str) -> ReviewRecord | None:
        return None


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
        selected_agent_versions=("correctness:v1",),
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

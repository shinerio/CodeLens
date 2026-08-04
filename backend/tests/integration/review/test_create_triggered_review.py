from pathlib import Path
from types import SimpleNamespace

import pytest

from codelens.review.application.create_triggered_review import (
    CreateTriggeredReview,
    CreateTriggeredReviewHandler,
)
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.workspace.domain.models import BranchScope, ReviewTarget
from codelens.workspace.domain.ports import RepositoryInfo, ScopePlan


class StaticPlanner:
    async def plan(self, _path: Path, _scope: object) -> ScopePlan:
        return ScopePlan("a" * 40, "b" * 40, ("src/app.py",), False, "branch")


class ChangingCapture:
    def __init__(self) -> None:
        self.version = 0

    async def capture(self, _path: Path, _plan: ScopePlan) -> object:
        self.version += 1
        return SimpleNamespace(
            target=ReviewTarget("a" * 40, f"{self.version:040x}", None),
            overlay_artifact=None,
        )


class CompleteFreezer:
    async def freeze(
        self, _profile: ReviewProfileSnapshot, prompt_locale: str
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "catalog_snapshot": {"version": "catalog:v1"},
            "capability_readiness": {"policy_fingerprint": "capability:v1"},
            "planner_execution_spec": {"artifact_id": "prompt:planner:v1"},
            "eligible_reviewer_execution_specs": [],
            "artifact_ids": [f"prompt:planner:v1:{prompt_locale}"],
        }


class LeakingFreezer(CompleteFreezer):
    async def freeze(
        self, profile: ReviewProfileSnapshot, prompt_locale: str
    ) -> dict[str, object]:
        context = await super().freeze(profile, prompt_locale)
        context["planner_execution_spec"] = {
            "artifact_id": "prompt:planner:v1",
            "prompt": "trusted prompt bytes must live in the Artifact Store",
        }
        return context


class RecordingTriggeredStore:
    def __init__(self) -> None:
        self.tasks: list[ReviewTask] = []

    async def create_triggered_with_job(self, task: ReviewTask) -> tuple[object, bool]:
        self.tasks.append(task)
        return SimpleNamespace(task_id=task.task_id), True


class NoopArtifacts:
    async def discard(self, _reference: str) -> None:
        raise AssertionError("no overlay was captured")


async def test_trigger_slot_excludes_snapshot_but_exact_key_includes_it(tmp_path: Path) -> None:
    store = RecordingTriggeredStore()
    handler = CreateTriggeredReviewHandler(  # type: ignore[arg-type]
        StaticPlanner(), ChangingCapture(), CompleteFreezer(), store, NoopArtifacts()
    )
    repository = RepositoryInfo(
        path=tmp_path,
        repository_id="repository-1",
        repository_realpath_hash="b" * 64,
        git_common_dir_hash="c" * 64,
        head_sha="d" * 40,
        current_branch="main",
        is_dirty=False,
    )
    command = CreateTriggeredReview(
        repository=repository,
        scope=BranchScope("main", "HEAD"),
        review_profile=ReviewProfileSnapshot(AdaptiveReviewerSelection()),
        prompt_locale="en",
        supersede_policy="latest_snapshot",
        external_context=None,
    )

    await handler.handle(command)
    await handler.handle(command)

    assert store.tasks[0].trigger_slot_key == store.tasks[1].trigger_slot_key
    assert store.tasks[0].idempotency_key != store.tasks[1].idempotency_key
    assert store.tasks[0].selected_agent_versions == ()
    store.tasks[0].verify_planning_context()


async def test_trigger_rejects_trusted_prompt_bodies_before_creating_task(
    tmp_path: Path,
) -> None:
    store = RecordingTriggeredStore()
    handler = CreateTriggeredReviewHandler(  # type: ignore[arg-type]
        StaticPlanner(), ChangingCapture(), LeakingFreezer(), store, NoopArtifacts()
    )
    repository = RepositoryInfo(
        path=tmp_path,
        repository_id="repository-1",
        repository_realpath_hash="b" * 64,
        git_common_dir_hash="c" * 64,
        head_sha="d" * 40,
        current_branch="main",
        is_dirty=False,
    )

    with pytest.raises(ValueError, match="trusted body fields"):
        await handler.handle(
            CreateTriggeredReview(
                repository=repository,
                scope=BranchScope("main", "HEAD"),
                review_profile=ReviewProfileSnapshot(
                    AdaptiveReviewerSelection()
                ),
                prompt_locale="en",
                supersede_policy="latest_snapshot",
                external_context=None,
            )
        )

    assert store.tasks == []


async def test_prompt_locale_is_part_of_the_frozen_trigger_policy(tmp_path: Path) -> None:
    store = RecordingTriggeredStore()
    handler = CreateTriggeredReviewHandler(  # type: ignore[arg-type]
        StaticPlanner(), ChangingCapture(), CompleteFreezer(), store, NoopArtifacts()
    )
    repository = RepositoryInfo(
        path=tmp_path,
        repository_id="repository-1",
        repository_realpath_hash="b" * 64,
        git_common_dir_hash="c" * 64,
        head_sha="d" * 40,
        current_branch="main",
        is_dirty=False,
    )
    profile = ReviewProfileSnapshot(AdaptiveReviewerSelection())

    for locale in ("en", "zh-CN"):
        await handler.handle(
            CreateTriggeredReview(
                repository=repository,
                scope=BranchScope("main", "HEAD"),
                review_profile=profile,
                prompt_locale=locale,
                supersede_policy="latest_snapshot",
                external_context=None,
            )
        )

    assert store.tasks[0].trigger_slot_key != store.tasks[1].trigger_slot_key
    assert store.tasks[0].idempotency_key != store.tasks[1].idempotency_key

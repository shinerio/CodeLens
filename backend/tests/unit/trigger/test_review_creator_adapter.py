from pathlib import Path
from types import SimpleNamespace

from codelens.findings.domain.existing_findings import ExistingFinding
from codelens.plugin.api.v2 import (
    FixedReviewerSelection,
    SupersedePolicy,
    TriggerReviewPolicy,
)
from codelens.trigger.application.review_creator_adapter import ReviewCreatorAdapter
from codelens.workspace.domain.ports import RepositoryInfo


class _RecordingCreateReviewHandler:
    def __init__(self) -> None:
        self.command: object | None = None

    async def handle(self, command: object) -> object:
        self.command = command
        return SimpleNamespace(task_id="review-triggered")


class _RepositoryInspector:
    async def inspect(self, repository: Path) -> RepositoryInfo:
        return RepositoryInfo(
            path=repository,
            repository_id="repository-1",
            repository_realpath_hash="a" * 64,
            git_common_dir_hash="b" * 64,
            head_sha="c" * 40,
            current_branch="feature",
            is_dirty=False,
        )


async def test_webhook_existing_findings_cross_only_the_structured_trigger_boundary(
    tmp_path: Path,
) -> None:
    handler = _RecordingCreateReviewHandler()
    adapter = ReviewCreatorAdapter(handler, _RepositoryInspector())  # type: ignore[arg-type]
    existing_finding = ExistingFinding(
        source_id="github",
        finding_id="PRRC_kwDO-example",
        title="Existing PR comment",
        content="This issue was reported on the prior PR revision.",
        path="src/service.py",
        side="new",
        start_line=12,
        end_line=12,
        existing_code="return account.name",
    )

    task_id = await adapter.create_review_from_trigger(
        repository_path=tmp_path,
        scope_type="branch",
        scope_params={"base_ref": "main", "target_ref": "feature"},
        review_policy=TriggerReviewPolicy(
            reviewer_selection=FixedReviewerSelection(
                mode="fixed", reviewer_versions=("general:v2",)
            ),
            supersede_policy=SupersedePolicy.LATEST_SNAPSHOT,
            prompt_locale="en",
        ),
        external_context={"platform": "github", "pull_request": 42},
        existing_findings=(existing_finding,),
    )

    assert task_id == "review-triggered"
    assert handler.command is not None
    assert handler.command.existing_findings == (existing_finding,)  # type: ignore[attr-defined]
    assert handler.command.external_context == {  # type: ignore[attr-defined]
        "platform": "github",
        "pull_request": 42,
    }

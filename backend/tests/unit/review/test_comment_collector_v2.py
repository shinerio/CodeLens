import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal

from codelens.findings.infrastructure.comment_output import CommentFindingSchema
from codelens.review.infrastructure.comment_collector import ReviewCommentCollector
from codelens.workspace.domain.models import (
    ChangedHunk,
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotEntry,
    SnapshotManifest,
    TaskWorktree,
)
from codelens.workspace.domain.review_file_scope import ReviewFileScope


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class FakeEvidenceTools:
    review_file_paths = ("src/webhook.py", "src/deleted.py")

    def __init__(self) -> None:
        self.reviewed_paths: set[str] = set()

    async def read_diff_for_resolution(self, path: str) -> str:
        if path == "src/deleted.py":
            content = "@@ -1 +0,0 @@\n-dangerous()\n"
        else:
            content = "@@ -2 +2 @@\n-parse(body)\n+payload = parse(body)\n"
        return json.dumps({"content": content})

    async def read_full_file(
        self,
        path: str,
        version: Literal["base", "current"],
    ) -> str:
        if path == "src/deleted.py" and version == "base":
            return "dangerous()\n"
        return "def handle() -> None:\n    payload = parse(body)\n"

    async def excerpt_identity(
        self,
        path: str,
        _start_line: int,
        _end_line: int,
        version: Literal["base", "current"],
    ) -> tuple[str, bool]:
        if path == "src/deleted.py" and version == "base":
            return _hash(b"dangerous()\n"), False
        return _hash(b"payload = parse(body)\n"), False


def _snapshot() -> ReviewSnapshot:
    worktree = TaskWorktree(
        "worktree-1",
        "review-1",
        "a" * 64,
        Path("/owned"),
        "b" * 40,
        "c" * 64,
    )
    return ReviewSnapshot(
        "snapshot-1",
        worktree,
        ReviewTarget("a" * 40, "b" * 40, None),
        RepositoryFingerprint("b" * 40, "d" * 64, "e" * 64),
        SnapshotManifest(
            ReviewFileScope.include_all(("src/webhook.py", "src/deleted.py")),
            entries=(
                SnapshotEntry("src/webhook.py", "file", 0o644, 22, "f" * 64, None, "target"),
                SnapshotEntry("src/deleted.py", "deleted", 0o644, 0, "1" * 64, None, "target"),
            ),
        ),
        ChangeIndex(
            (
                ChangedHunk(
                    "new-hunk",
                    "src/webhook.py",
                    2,
                    2,
                    "new",
                    _hash(b"payload = parse(body)\n"),
                ),
                ChangedHunk(
                    "old-hunk",
                    "src/deleted.py",
                    1,
                    1,
                    "old",
                    _hash(b"dangerous()\n"),
                ),
            )
        ),
    )


def _submission(**overrides: object) -> CommentFindingSchema:
    payload: dict[str, object] = {
        "reviewer_id": "security",
        "path": "src/webhook.py",
        "side": "new",
        "existing_code": "payload = parse(body)",
        "title": "Body parsed before signature verification",
        "content": "Untrusted input is parsed before authentication.",
        "recommendation": "Verify the signature before parsing.",
        "category": "authentication",
        "severity": "high",
        "primary_dimension": "security",
        "evidence_strength": "direct",
        **overrides,
    }
    return CommentFindingSchema.model_validate(payload)


def _collector(
    *,
    reviewer_reference: str = "security:v2",
    reviewer_dimensions: tuple[str, ...] = ("security",),
    review_feedback: str | None = None,
) -> ReviewCommentCollector:
    return ReviewCommentCollector(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        reviewer_reference=reviewer_reference,
        reviewer_dimensions=reviewer_dimensions,
        tools=FakeEvidenceTools(),
        review_feedback=review_feedback,
    )


def test_incomplete_completion_reports_host_derived_coverage_progress() -> None:
    collector = _collector()
    assert isinstance(collector.tools, FakeEvidenceTools)
    collector.tools.reviewed_paths.add("src/webhook.py")

    completion = json.loads(collector.complete("Not complete yet."))

    assert completion["status"] == "needs_action"
    assert completion["data"] == {
        "active_comment_count": 0,
        "incomplete_retry_count": 1,
        "max_incomplete_review_retries": 3,
        "missing_file_count": 1,
        "missing_review_files": ["src/deleted.py"],
        "reviewed_file_count": 1,
        "total_review_file_count": 2,
    }

    repeated_completion = json.loads(collector.complete("Still incomplete."))
    assert repeated_completion["status"] == "needs_action"
    assert repeated_completion["diagnostics"][0]["code"] == "missing_review_files"
    assert "Do not call task_done again" in repeated_completion["diagnostics"][0]["message"]


def test_complete_completion_reports_host_derived_coverage_progress() -> None:
    collector = _collector()
    assert isinstance(collector.tools, FakeEvidenceTools)
    collector.tools.reviewed_paths.update(collector.tools.review_file_paths)

    completion = json.loads(collector.complete("Complete."))

    assert completion["status"] == "success"
    assert completion["data"] == {
        "active_comment_count": 0,
        "forced_completion": False,
        "incomplete_files": [],
        "missing_file_count": 0,
        "reviewed_file_count": 2,
        "total_review_file_count": 2,
    }


async def test_collector_resolves_new_and_deleted_old_locations() -> None:
    collector = _collector()

    await collector.submit(_submission())
    await collector.submit(
        _submission(
            path="src/deleted.py",
            side="old",
            existing_code="dangerous()",
            title="Authentication guard removed",
        )
    )

    batch = collector.candidate_batch()
    assert batch.schema_version == "2"
    assert [candidate.changed_hunk_id for candidate in batch.candidates] == [
        "new-hunk",
        "old-hunk",
    ]
    assert batch.candidates[1].primary_location.is_deleted is True
    assert batch.candidates[0].existing_code_hash == _hash(b"payload = parse(body)")
    assert not hasattr(batch.candidates[0], "confidence")


async def test_collector_rejects_items_independently() -> None:
    collector = _collector()

    result = json.loads(
        await collector.submit_many([_submission(path="outside.py"), _submission()])
    )

    assert result["status"] == "partial"
    assert result["data"]["accepted_count"] == 1
    assert result["data"]["rejected_comments"] == [
        {
            "input_index": 0,
            "code": "path_outside_review",
            "message": "comment path is outside this Review",
        }
    ]
    assert len(collector.candidate_batch().candidates) == 1


async def test_collector_returns_localized_feedback_for_an_unchanged_location() -> None:
    feedback = "请聚焦本次代码修改，并将意见提在本次修改的代码行上。"
    collector = _collector(review_feedback=feedback)

    result = json.loads(
        await collector.submit_many(
            [_submission(existing_code="def handle() -> None:", title="Unchanged declaration")]
        )
    )

    assert result["status"] == "rejected"
    assert result["data"]["accepted_count"] == 0
    assert result["data"]["rejected_comments"] == [
        {
            "input_index": 0,
            "code": "comment_outside_diff",
            "message": feedback,
        }
    ]
    assert collector.candidate_batch().candidates == ()


async def test_collector_rejects_duplicate_candidate_identity() -> None:
    collector = _collector()

    result = json.loads(await collector.submit_many([_submission(), _submission()]))

    assert result["data"]["accepted_count"] == 1
    assert result["data"]["rejected_comments"] == [
        {
            "input_index": 1,
            "code": "duplicate_comment",
            "message": "comment duplicates an active candidate",
        }
    ]


async def test_specialist_primary_dimension_must_match_assignment() -> None:
    collector = _collector()

    result = json.loads(await collector.submit_many([_submission(primary_dimension="performance")]))

    assert result["data"]["accepted_count"] == 0
    assert result["data"]["rejected_comments"][0]["input_index"] == 0
    assert "primary dimension" in result["data"]["rejected_comments"][0]["message"]


async def test_general_accepts_any_dimension_in_its_declared_scope() -> None:
    collector = _collector(
        reviewer_reference="general:v2",
        reviewer_dimensions=(
            "correctness",
            "security",
            "reliability-concurrency",
            "contract-data",
            "architecture",
            "performance",
            "test-regression",
        ),
    )

    result = json.loads(await collector.submit_many([_submission(reviewer_id="general")]))

    assert result["data"]["accepted_count"] == 1


async def test_comment_returns_candidate_ids_and_retraction_is_auditable() -> None:
    collector = _collector()
    submitted = json.loads(await collector.submit_many([_submission()]))
    candidate_id = submitted["data"]["accepted_comments"][0]["candidate_id"]

    retracted = json.loads(
        collector.retract_many([candidate_id], "Later evidence disproves the claim.")
    )
    repeated = json.loads(
        collector.retract_many([candidate_id], "Later evidence disproves the claim.")
    )

    assert submitted["status"] == "success"
    assert candidate_id.startswith("candidate_")
    assert retracted["status"] == "success"
    assert retracted["data"]["retracted_count"] == 1
    assert collector.candidate_batch().candidates == ()
    assert collector.candidate_audit == (
        (
            candidate_id,
            (
                ("active", None),
                ("retracted", "Later evidence disproves the claim."),
            ),
        ),
    )
    assert repeated["status"] == "success"
    assert repeated["data"]["already_retracted_count"] == 1
    assert repeated["diagnostics"][0]["code"] == "no_state_change"


async def test_retraction_mixed_unknown_and_retracted_is_partial() -> None:
    collector = _collector()
    submitted = json.loads(await collector.submit_many([_submission()]))
    candidate_id = submitted["data"]["accepted_comments"][0]["candidate_id"]

    result = json.loads(collector.retract_many([candidate_id, "candidate_other_run"], "Incorrect."))

    assert result["status"] == "partial"
    assert result["data"]["retracted_count"] == 1
    assert result["data"]["unknown_count"] == 1
    assert result["diagnostics"][0]["code"] == "unknown_candidate"


async def test_retracted_comment_can_be_resubmitted_at_end_of_active_order() -> None:
    collector = _collector()
    first = json.loads(await collector.submit_many([_submission()]))
    first_id = first["data"]["accepted_comments"][0]["candidate_id"]
    collector.retract_many([first_id], "Incorrect.")

    second = json.loads(await collector.submit_many([_submission()]))
    second_id = second["data"]["accepted_comments"][0]["candidate_id"]

    assert second_id != first_id
    assert [item.candidate_id for item in collector.candidate_batch().candidates] == [second_id]


async def test_completion_freezes_comment_state_and_uses_active_count() -> None:
    collector = _collector()
    assert isinstance(collector.tools, FakeEvidenceTools)
    collector.tools.reviewed_paths.update(collector.tools.review_file_paths)
    submitted = json.loads(await collector.submit_many([_submission()]))
    candidate_id = submitted["data"]["accepted_comments"][0]["candidate_id"]

    completion = json.loads(collector.complete("Finished."))
    late_retraction = json.loads(collector.retract_many([candidate_id], "Too late."))
    late_comment = json.loads(await collector.submit_many([_submission()]))
    repeated_completion = json.loads(collector.complete("Again."))

    assert completion["status"] == "success"
    assert completion["data"]["active_comment_count"] == 1
    assert late_retraction["diagnostics"][0]["code"] == "reviewer_already_completed"
    assert late_comment["diagnostics"][0]["code"] == "reviewer_already_completed"
    assert repeated_completion["diagnostics"][0]["code"] == "reviewer_already_completed"


async def test_concurrent_duplicate_comments_leave_one_active_candidate() -> None:
    collector = _collector()

    first, second = await asyncio.gather(
        collector.submit_many([_submission()]),
        collector.submit_many([_submission()]),
    )

    results = [json.loads(first), json.loads(second)]
    assert sorted(result["status"] for result in results) == ["rejected", "success"]
    assert collector.active_comment_count == 1


async def test_retraction_rejects_all_unknown_duplicate_ids_and_blank_reason() -> None:
    collector = _collector()

    unknown = json.loads(collector.retract_many(["candidate_from_another_run"], "Not this run."))
    duplicates = json.loads(
        collector.retract_many(["candidate_x", "candidate_x"], "Duplicate input.")
    )
    blank_reason = json.loads(collector.retract_many(["candidate_x"], "   "))

    assert unknown["status"] == "rejected"
    assert unknown["data"]["unknown_count"] == 1
    assert unknown["diagnostics"][0]["code"] == "unknown_candidate"
    assert duplicates["status"] == "rejected"
    assert duplicates["diagnostics"][0]["code"] == "invalid_argument_value"
    assert blank_reason["status"] == "rejected"

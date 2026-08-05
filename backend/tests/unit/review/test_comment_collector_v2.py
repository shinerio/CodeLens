import hashlib
import json
from pathlib import Path
from typing import Literal

from codelens.findings.infrastructure.comment_v2_output import CommentV2FindingSchema
from codelens.review.infrastructure.comment_collector_v2 import ReviewCommentCollectorV2
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


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class FakeEvidenceTools:
    review_file_paths = ("src/webhook.py", "src/deleted.py")

    async def read_diff_for_resolution(self, path: str) -> str:
        if path == "src/deleted.py":
            content = "@@ -1 +0,0 @@\n-dangerous()\n"
        else:
            content = "@@ -1 +1 @@\n-parse(body)\n+payload = parse(body)\n"
        return json.dumps({"content": content})

    async def read_full_file(
        self,
        path: str,
        version: Literal["base", "current"],
    ) -> str:
        if path == "src/deleted.py" and version == "base":
            return "dangerous()\n"
        return "payload = parse(body)\n"

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
            ("src/webhook.py", "src/deleted.py"),
            (),
            (),
            entries=(
                SnapshotEntry(
                    "src/webhook.py", "file", 0o644, 22, "f" * 64, None, "target"
                ),
                SnapshotEntry(
                    "src/deleted.py", "deleted", 0o644, 0, "1" * 64, None, "target"
                ),
            ),
        ),
        ChangeIndex(
            (
                ChangedHunk(
                    "new-hunk",
                    "src/webhook.py",
                    1,
                    1,
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


def _submission(**overrides: object) -> CommentV2FindingSchema:
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
    return CommentV2FindingSchema.model_validate(payload)


def _collector(
    *,
    reviewer_reference: str = "security:v1",
    reviewer_dimensions: tuple[str, ...] = ("security",),
) -> ReviewCommentCollectorV2:
    return ReviewCommentCollectorV2(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        reviewer_reference=reviewer_reference,
        reviewer_dimensions=reviewer_dimensions,
        tools=FakeEvidenceTools(),
    )


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
        await collector.submit_many(
            [_submission(path="outside.py"), _submission()]
        )
    )

    assert result["accepted_count"] == 1
    assert result["rejected_comments"] == [
        {"index": 0, "reason": "comment path is outside this Review"}
    ]
    assert len(collector.candidate_batch().candidates) == 1


async def test_collector_rejects_duplicate_candidate_identity() -> None:
    collector = _collector()

    result = json.loads(await collector.submit_many([_submission(), _submission()]))

    assert result["accepted_count"] == 1
    assert result["rejected_comments"] == [
        {"index": 1, "reason": "comment duplicates an accepted candidate"}
    ]


async def test_specialist_primary_dimension_must_match_assignment() -> None:
    collector = _collector()

    result = json.loads(
        await collector.submit_many(
            [_submission(primary_dimension="performance")]
        )
    )

    assert result["accepted_count"] == 0
    assert result["rejected_comments"][0]["index"] == 0
    assert "primary dimension" in result["rejected_comments"][0]["reason"]


async def test_general_accepts_any_dimension_in_its_declared_scope() -> None:
    collector = _collector(
        reviewer_reference="general:v1",
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

    result = json.loads(
        await collector.submit_many([_submission(reviewer_id="general")])
    )

    assert result["accepted_count"] == 1

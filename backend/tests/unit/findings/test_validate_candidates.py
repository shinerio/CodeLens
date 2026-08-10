from dataclasses import replace
from pathlib import Path

import pytest

from codelens.findings.application.validate_candidates import (
    CandidateBatchCodec,
    CandidateValidator,
)
from codelens.findings.domain.candidates import (
    CandidateFinding,
    CandidateFindingBatch,
    EvidenceStrength,
)
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
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


def candidate(candidate_id: str, *, reviewer: str, path: str, line: int) -> CandidateFinding:
    return CandidateFinding(
        task_id="review-1",
        candidate_id=candidate_id,
        run_id="run-1",
        snapshot_id="snapshot-1",
        reviewer_reference=reviewer,
        category="authentication",
        title="Signature checked after parsing",
        severity=FindingSeverity.HIGH,
        primary_dimension="security",
        evidence_strength=EvidenceStrength.DIRECT,
        primary_location=SourceLocation(path, line, line, "new", "a" * 64, False),
        related_locations=(),
        changed_hunk_id="hunk-1",
        existing_code_hash="a" * 64,
        evidence_hashes=("a" * 64,),
        content="The request body is parsed before its signature is checked.",
        recommendation="Verify the signature before parsing.",
        fingerprint="b" * 64,
    )


def _snapshot() -> ReviewSnapshot:
    return ReviewSnapshot(
        "snapshot-1",
        TaskWorktree("worktree-1", "review-1", "c" * 64, Path("/tmp/review"), "d" * 40, "e" * 64),
        ReviewTarget("a" * 40, "b" * 40, None),
        RepositoryFingerprint("b" * 40, "f" * 64, "1" * 64),
        SnapshotManifest(
            ReviewFileScope.include_all(("src/webhook.py",)),
            entries=(
                SnapshotEntry("src/webhook.py", "file", 0o100644, 20, "2" * 64, None, "target"),
            ),
        ),
        ChangeIndex((ChangedHunk("hunk-1", "src/webhook.py", 5, 5, "new", "a" * 64),)),
    )


async def test_candidate_validator_accepts_only_matching_snapshot_and_reviewer() -> None:
    validator = CandidateValidator(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        agent=builtin_agent_catalog()["security:v2"],
    )
    value = candidate(
        "candidate_" + "c" * 64,
        reviewer="security:v2",
        path="src/webhook.py",
        line=5,
    )

    assert await validator.validate(CandidateFindingBatch((value,))) == CandidateFindingBatch(
        (value,)
    )
    codec = CandidateBatchCodec()
    assert codec.decode(codec.encode(CandidateFindingBatch((value,)))) == CandidateFindingBatch(
        (value,)
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"changed_hunk_id": "hunk-unknown"}, "hunk"),
        ({"evidence_hashes": ("z" * 64,)}, "evidence"),
        ({"reviewer_reference": "performance:v2"}, "reviewer"),
    ),
)
async def test_candidate_validator_skips_invalid_candidate_and_records_warning(
    mutation: dict[str, object], message: str
) -> None:
    validator = CandidateValidator(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        agent=builtin_agent_catalog()["security:v2"],
    )
    value = replace(
        candidate(
            "candidate_" + "c" * 64,
            reviewer="security:v2",
            path="src/webhook.py",
            line=5,
        ),
        **mutation,
    )

    result = await validator.validate(CandidateFindingBatch((value,)))

    assert len(result.candidates) == 0
    assert len(validator.warnings) == 1
    assert validator.warnings[0].reason_code == "invalid"
    assert message in validator.warnings[0].message


async def test_candidate_validator_best_effort_keeps_valid_and_skips_invalid() -> None:
    """A batch with mixed valid/invalid candidates retains only valid ones."""
    validator = CandidateValidator(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        agent=builtin_agent_catalog()["security:v2"],
    )
    valid = candidate(
        "candidate_" + "c" * 64,
        reviewer="security:v2",
        path="src/webhook.py",
        line=5,
    )
    invalid = replace(
        candidate(
            "candidate_" + "d" * 64,
            reviewer="security:v2",
            path="src/webhook.py",
            line=5,
        ),
        evidence_hashes=("z" * 64,),
    )

    result = await validator.validate(CandidateFindingBatch((valid, invalid)))

    assert len(result.candidates) == 1
    assert result.candidates[0].candidate_id == valid.candidate_id
    assert len(validator.warnings) == 1
    assert validator.warnings[0].reason_code == "invalid"
    assert "evidence" in validator.warnings[0].message


async def test_candidate_validator_deduplicates_by_fingerprint() -> None:
    """Candidates with the same fingerprint are deduplicated with a warning."""
    validator = CandidateValidator(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        agent=builtin_agent_catalog()["security:v2"],
    )
    first = candidate(
        "candidate_" + "c" * 64,
        reviewer="security:v2",
        path="src/webhook.py",
        line=5,
    )
    second = candidate(
        "candidate_" + "d" * 64,
        reviewer="security:v2",
        path="src/webhook.py",
        line=5,
    )

    result = await validator.validate(CandidateFindingBatch((first, second)))

    assert len(result.candidates) == 1
    assert result.candidates[0].candidate_id == first.candidate_id
    assert len(validator.warnings) == 1
    assert validator.warnings[0].reason_code == "duplicate"

from dataclasses import replace
from pathlib import Path

import pytest

from codelens.findings.application.validate_candidates import (
    CandidateBatchCodec,
    CandidateValidationError,
    CandidateValidator,
)
from codelens.findings.domain.candidates import (
    CandidateFinding,
    CandidateFindingBatch,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
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


def candidate(
    candidate_id: str, *, reviewer: str, path: str, line: int
) -> CandidateFinding:
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
        secondary_dimensions=(),
        evidence_strength=EvidenceStrength.DIRECT,
        impact_certainty=ImpactCertainty.CONFIRMED,
        reproducibility=Reproducibility.DETERMINISTIC,
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
            ("src/webhook.py",),
            (),
            (),
            entries=(
                SnapshotEntry(
                    "src/webhook.py", "file", 0o100644, 20, "2" * 64, None, "target"
                ),
            ),
        ),
        ChangeIndex(
            (ChangedHunk("hunk-1", "src/webhook.py", 5, 5, "new", "a" * 64),)
        ),
    )


async def test_candidate_validator_accepts_only_matching_snapshot_and_reviewer() -> None:
    validator = CandidateValidator(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        agent=builtin_agent_catalog()["security:v1"],
    )
    value = candidate(
        "candidate_" + "c" * 64,
        reviewer="security:v1",
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
        ({"reviewer_reference": "performance:v1"}, "reviewer"),
    ),
)
async def test_candidate_validator_rejects_invalid_location_evidence_or_identity(
    mutation: dict[str, object], message: str
) -> None:
    validator = CandidateValidator(
        task_id="review-1",
        run_id="run-1",
        snapshot=_snapshot(),
        agent=builtin_agent_catalog()["security:v1"],
    )
    value = replace(
        candidate(
            "candidate_" + "c" * 64,
            reviewer="security:v1",
            path="src/webhook.py",
            line=5,
        ),
        **mutation,
    )

    with pytest.raises(CandidateValidationError, match=message):
        await validator.validate(CandidateFindingBatch((value,)))

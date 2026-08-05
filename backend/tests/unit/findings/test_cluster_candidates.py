from dataclasses import replace

from codelens.findings.application.cluster_candidates import CandidateClusterer
from codelens.findings.domain.candidates import (
    CandidateFinding,
    EvidenceStrength,
)
from codelens.findings.domain.models import FindingSeverity, SourceLocation


def candidate(
    candidate_id: str, *, reviewer: str, path: str, line: int
) -> CandidateFinding:
    return CandidateFinding(
        task_id="review-1",
        candidate_id=candidate_id,
        run_id=f"run-{reviewer}",
        snapshot_id="snapshot-1",
        reviewer_reference=reviewer,
        category="cache-invalidation",
        title="Stale entry survives invalidation",
        severity=FindingSeverity.HIGH,
        primary_dimension="correctness",
        evidence_strength=EvidenceStrength.DIRECT,
        primary_location=SourceLocation(path, line, line, "new", "a" * 64, False),
        related_locations=(),
        changed_hunk_id="hunk-1",
        existing_code_hash="b" * 64,
        evidence_hashes=("b" * 64,),
        content="The invalidation leaves the old cache entry reachable.",
        recommendation="Remove the old entry during invalidation.",
        fingerprint=(candidate_id[-1:] or "c") * 64,
    )


def test_candidates_with_same_location_root_cause_and_impact_cluster_together() -> None:
    clusters = CandidateClusterer().cluster(
        (
            candidate(
                "candidate-a", reviewer="correctness:v2", path="src/cache.py", line=40
            ),
            candidate(
                "candidate-b", reviewer="security:v1", path="src/cache.py", line=40
            ),
        )
    )

    assert len(clusters) == 1
    assert clusters[0].candidate_ids == ("candidate-a", "candidate-b")


def test_distinct_root_causes_at_same_line_do_not_cluster() -> None:
    first = candidate(
        "candidate-a", reviewer="correctness:v2", path="src/cache.py", line=40
    )
    second = replace(
        candidate(
            "candidate-b", reviewer="security:v1", path="src/cache.py", line=40
        ),
        category="authorization",
        title="Missing tenant boundary",
    )

    assert len(CandidateClusterer().cluster((first, second))) == 2


def test_cluster_ids_and_membership_are_deterministic_and_keep_single_reporters() -> None:
    first = candidate(
        "candidate-a", reviewer="correctness:v2", path="src/cache.py", line=40
    )
    second = candidate(
        "candidate-b", reviewer="security:v1", path="src/cache.py", line=40
    )
    single = candidate(
        "candidate-c", reviewer="performance:v1", path="src/slow.py", line=8
    )
    clusterer = CandidateClusterer()

    forward = clusterer.cluster((first, second, single))
    reverse = clusterer.cluster((single, second, first))

    assert forward == reverse
    assert any(cluster.candidate_ids == ("candidate-c",) for cluster in forward)


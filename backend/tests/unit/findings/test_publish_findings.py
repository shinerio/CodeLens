from codelens.findings.application.publish_findings import FindingPublisher
from codelens.findings.domain.candidates import (
    CandidateFinding,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.findings.domain.resolution import (
    FindingCluster,
    ResolutionDecision,
    VerificationDecision,
)


def candidate(candidate_id: str, reviewer: str) -> CandidateFinding:
    return CandidateFinding(
        task_id="review-1",
        candidate_id=candidate_id,
        run_id=f"run-{reviewer}",
        snapshot_id="snapshot-1",
        reviewer_reference=reviewer,
        category="authentication",
        title="Missing signature check",
        severity=FindingSeverity.HIGH,
        primary_dimension="security",
        secondary_dimensions=("correctness",),
        evidence_strength=EvidenceStrength.DIRECT,
        impact_certainty=ImpactCertainty.CONFIRMED,
        reproducibility=Reproducibility.DETERMINISTIC,
        primary_location=SourceLocation(
            "src/webhook.py", 5, 5, "new", "a" * 64, False
        ),
        related_locations=(),
        changed_hunk_id="hunk-a",
        existing_code_hash="a" * 64,
        evidence_hashes=("a" * 64,),
        content="Unsigned requests are accepted.",
        recommendation="Verify signatures first.",
        fingerprint=("b" if candidate_id.endswith("a") else "c") * 64,
    )


def test_v2_publication_has_nullable_confidence_axes_and_provenance() -> None:
    candidates = (
        candidate("candidate-a", "security:v1"),
        candidate("candidate-b", "general:v1"),
    )
    cluster = FindingCluster(
        "cluster-a", tuple(item.candidate_id for item in candidates)
    )
    resolution = ResolutionDecision.publish(
        cluster=cluster,
        canonical_candidate_id="candidate-a",
        merged_candidate_ids=("candidate-a", "candidate-b"),
        severity=FindingSeverity.HIGH,
        title="Missing signature check",
        content="Unsigned requests are accepted.",
        recommendation="Verify signatures first.",
    )

    findings = FindingPublisher.build(
        task_id="review-1", candidates=candidates, resolutions=(resolution,)
    )

    assert len(findings) == 1
    assert findings[0].confidence is None
    assert findings[0].primary_dimension == "security"
    assert findings[0].evidence_strength == "direct"
    assert findings[0].source_reviewer_references == (
        "general:v1",
        "security:v1",
    )


def test_only_confirmed_verify_decision_is_published() -> None:
    source = candidate("candidate-a", "security:v1")
    cluster = FindingCluster("cluster-a", (source.candidate_id,))
    resolution = ResolutionDecision.verify(
        cluster=cluster,
        canonical_candidate_id=source.candidate_id,
        merged_candidate_ids=(source.candidate_id,),
        severity=source.severity,
        title=source.title,
        content=source.content,
        recommendation=source.recommendation,
    )

    unresolved = VerificationDecision.unresolved(
        target_id=cluster.cluster_id,
        batch_target_ids=(cluster.cluster_id,),
    )
    confirmed = VerificationDecision.confirmed(
        target_id=cluster.cluster_id,
        batch_target_ids=(cluster.cluster_id,),
    )

    assert FindingPublisher.build(
        task_id="review-1",
        candidates=(source,),
        resolutions=(resolution,),
        verifications=(unresolved,),
    ) == ()
    assert len(
        FindingPublisher.build(
            task_id="review-1",
            candidates=(source,),
            resolutions=(resolution,),
            verifications=(confirmed,),
        )
    ) == 1

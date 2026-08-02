import pytest

from codelens.findings.application.resolve_clusters import validate_resolution
from codelens.findings.domain.candidates import (
    CandidateFinding,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.findings.domain.resolution import FindingCluster, ResolutionDecision
from codelens.findings.infrastructure.resolver_output import (
    ResolutionDecisionDto,
    ResolutionValidationError,
    ResolverOutputCodec,
    ResolverSubmissionDto,
)


def _candidate(candidate_id: str, severity: str) -> CandidateFinding:
    return CandidateFinding(
        task_id="review-1",
        candidate_id=candidate_id,
        run_id="run-1",
        snapshot_id="snapshot-1",
        reviewer_reference="security:v1",
        category="authentication",
        title="Signature checked after parsing",
        severity=FindingSeverity(severity),
        primary_dimension="security",
        secondary_dimensions=(),
        evidence_strength=EvidenceStrength.DIRECT,
        impact_certainty=ImpactCertainty.CONFIRMED,
        reproducibility=Reproducibility.DETERMINISTIC,
        primary_location=SourceLocation("src/webhook.py", 5, 5, "new", "a" * 64, False),
        related_locations=(),
        changed_hunk_id="hunk-1",
        existing_code_hash="a" * 64,
        evidence_hashes=("a" * 64,),
        content="The request body is parsed before signature verification.",
        recommendation="Verify the signature first.",
        fingerprint=(candidate_id[-1:] or "b") * 64,
    )


def _candidates_with_severities(*severities: str) -> tuple[CandidateFinding, ...]:
    return tuple(
        _candidate(f"candidate-{index}", severity)
        for index, severity in enumerate(severities, start=1)
    )


def cluster_with_severities(*severities: str) -> FindingCluster:
    candidates = _candidates_with_severities(*severities)
    return FindingCluster("cluster-1", tuple(item.candidate_id for item in candidates))


def publish_resolution(*, severity: str) -> ResolutionDecision:
    cluster = cluster_with_severities("medium", "low")
    return ResolutionDecision.publish(
        cluster=cluster,
        canonical_candidate_id="candidate-1",
        merged_candidate_ids=("candidate-1", "candidate-2"),
        severity=FindingSeverity(severity),
        title="Signature checked after parsing",
        content="The request body is parsed before signature verification.",
        recommendation="Verify the signature first.",
        reason_code="confirmed-direct-evidence",
    )


def test_resolver_cannot_raise_severity_above_all_candidates() -> None:
    cluster = cluster_with_severities("medium", "low")
    candidates = _candidates_with_severities("medium", "low")

    with pytest.raises(ResolutionValidationError, match="severity"):
        validate_resolution(
            cluster, candidates, publish_resolution(severity="high")
        )


def test_resolver_rejects_unknown_candidates_and_persists_suppress_decision() -> None:
    cluster = cluster_with_severities("high", "medium")
    candidates = _candidates_with_severities("high", "medium")
    codec = ResolverOutputCodec((cluster,), candidates)
    unknown = ResolverSubmissionDto(
        schema_version="1",
        decisions=[
            ResolutionDecisionDto(
                cluster_id="cluster-1",
                outcome="publish",
                canonical_candidate_id="candidate-1",
                merged_candidate_ids=["candidate-1", "candidate-unknown"],
                severity="high",
                title="Signature checked after parsing",
                content="The request body is parsed before signature verification.",
                recommendation="Verify the signature first.",
                reason_code="confirmed-direct-evidence",
            )
        ],
    )
    with pytest.raises(ResolutionValidationError, match="candidate"):
        codec.decode(unknown)

    suppressed = ResolutionDecision.suppress(
        cluster=cluster, reason_code="insufficient-evidence"
    )
    decisions = codec.validate((suppressed,))

    assert decisions == (
        ResolutionDecision.suppress(
            cluster=cluster, reason_code="insufficient-evidence"
        ),
    )

from codelens.findings.application.publish_findings import FindingPublisher
from codelens.findings.domain.candidates import (
    CandidateFinding,
    EvidenceStrength,
)
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.findings.domain.verdict import VerdictDecision


def candidate(
    candidate_id: str,
    reviewer: str,
    severity: FindingSeverity = FindingSeverity.HIGH,
) -> CandidateFinding:
    """Build a minimal CandidateFinding for publication tests."""
    return CandidateFinding(
        task_id="review-1",
        candidate_id=candidate_id,
        run_id=f"run-{reviewer}",
        snapshot_id="snapshot-1",
        reviewer_reference=reviewer,
        category="authentication",
        title="Missing signature check",
        severity=severity,
        primary_dimension="security",
        evidence_strength=EvidenceStrength.DIRECT,
        primary_location=SourceLocation("src/webhook.py", 5, 5, "new", "a" * 64, False),
        related_locations=(),
        changed_hunk_id="hunk-a",
        existing_code_hash="a" * 64,
        evidence_hashes=("a" * 64,),
        content="Unsigned requests are accepted.",
        recommendation="Verify signatures first.",
        fingerprint=("b" if candidate_id.endswith("a") else "c") * 64,
    )


def cluster_for(
    cluster_id: str,
    candidates: tuple[CandidateFinding, ...],
    *,
    title: str = "Missing signature check",
    category: str = "authentication",
    severity: FindingSeverity = FindingSeverity.HIGH,
    content: str = "Unsigned requests are accepted.",
    recommendation: str = "Verify signatures first.",
    primary_dimension: str = "security",
    evidence_strength: EvidenceStrength = EvidenceStrength.DIRECT,
) -> FindingCluster:
    """Build a FindingCluster copying canonical fields from the first candidate.

    The canonical_candidate_id is the lexicographically first candidate id, matching
    the deterministic rule documented on FindingCluster.
    """
    canonical_id = min(item.candidate_id for item in candidates)
    return FindingCluster(
        cluster_id=cluster_id,
        candidate_ids=tuple(item.candidate_id for item in candidates),
        canonical_candidate_id=canonical_id,
        title=title,
        category=category,
        severity=severity,
        content=content,
        recommendation=recommendation,
        primary_dimension=primary_dimension,
        evidence_strength=evidence_strength,
    )


def test_accept_verdict_publishes_finding_with_cluster_canonical_fields() -> None:
    """ACCEPT verdict publishes one Finding using the cluster's canonical fields."""
    candidate_a = candidate("candidate-a", "security:v2")
    candidate_b = candidate("candidate-b", "general:v2")
    clusters = (cluster_for("cluster-a", (candidate_a, candidate_b)),)
    verdicts = (VerdictDecision.accept(cluster_ids=("cluster-a",)),)

    findings = FindingPublisher.build(
        task_id="review-1",
        candidates=(candidate_a, candidate_b),
        verdicts=verdicts,
        clusters=clusters,
    )

    assert len(findings) == 1
    finding = findings[0]
    cluster = clusters[0]
    # Cluster canonical fields are copied onto the Finding.
    assert finding.title == cluster.title
    assert finding.category == cluster.category
    assert finding.severity == cluster.severity
    assert finding.recommendation == cluster.recommendation
    assert finding.primary_dimension == cluster.primary_dimension
    assert finding.evidence_strength == cluster.evidence_strength.value
    # Canonical candidate's reviewer/location provenance is preserved.
    canonical = candidate_a
    assert finding.reviewer_id == canonical.reviewer_reference
    assert finding.primary_location == canonical.primary_location
    assert finding.changed_hunk_id == canonical.changed_hunk_id
    # ACCEPT over one cluster unions all member reviewer references.
    assert finding.source_reviewer_references == (
        "general:v2",
        "security:v2",
    )
    assert finding.confidence is None
    assert finding.disposition.value == "blocking"


def test_merge_verdict_publishes_finding_with_verdict_merge_fields() -> None:
    """MERGE verdict publishes one Finding using the model-synthesized fields."""
    candidate_a = candidate("candidate-a", "security:v2", FindingSeverity.LOW)
    candidate_b = candidate("candidate-b", "general:v2", FindingSeverity.LOW)
    clusters = (
        cluster_for(
            "cluster-a",
            (candidate_a, candidate_b),
            severity=FindingSeverity.LOW,
        ),
    )
    verdicts = (
        VerdictDecision.merge(
            cluster_ids=("cluster-a",),
            path="src/webhook.py",
            side="new",
            existing_code="def verify(): pass",
            title="Merged signature gap",
            content="Multiple reviewers flagged the same unsigned path.",
            recommendation="Add signature verification to the decorator chain.",
            category="authentication",
            severity=FindingSeverity.CRITICAL,
            primary_dimension="security",
            evidence_strength=EvidenceStrength.DIRECT,
            primary_location=SourceLocation("src/overridden.py", 8, 9, "old", "d" * 64, False),
            changed_hunk_id="hunk-overridden",
        ),
    )

    findings = FindingPublisher.build(
        task_id="review-1",
        candidates=(candidate_a, candidate_b),
        verdicts=verdicts,
        clusters=clusters,
    )

    assert len(findings) == 1
    finding = findings[0]
    verdict = verdicts[0]
    # MERGE path must copy the verdict's synthesized fields, not the cluster's.
    assert finding.title == verdict.title
    assert finding.category == verdict.category
    assert finding.severity == verdict.severity
    assert finding.severity is FindingSeverity.CRITICAL
    assert finding.recommendation == verdict.recommendation
    assert finding.primary_dimension == verdict.primary_dimension
    assert finding.evidence_strength == verdict.evidence_strength.value
    # impact/explanation mirror the merged content.
    assert finding.impact == verdict.content
    assert finding.explanation == verdict.content
    # Reviewer provenance remains host-owned while merge location is fully overridden.
    assert finding.reviewer_id == candidate_a.reviewer_reference
    assert finding.primary_location == verdict.primary_location
    assert finding.changed_hunk_id == "hunk-overridden"
    assert finding.source_reviewer_references == (
        "general:v2",
        "security:v2",
    )


def test_deny_verdict_publishes_no_finding() -> None:
    """DENY verdict suppresses publication; no Finding is emitted."""
    candidate_a = candidate("candidate-a", "security:v2")
    clusters = (cluster_for("cluster-a", (candidate_a,)),)
    verdicts = (VerdictDecision.deny(cluster_ids=("cluster-a",)),)

    findings = FindingPublisher.build(
        task_id="review-1",
        candidates=(candidate_a,),
        verdicts=verdicts,
        clusters=clusters,
    )

    assert findings == ()


def test_merge_verdict_over_multiple_clusters_unions_reviewer_references() -> None:
    """A MERGE verdict spanning multiple clusters unions all member reviewers."""
    candidate_a = candidate("candidate-a", "security:v2")
    candidate_b = candidate("candidate-b", "general:v2")
    candidate_c = candidate("candidate-c", "performance:v2")
    clusters = (
        cluster_for("cluster-a", (candidate_a, candidate_b)),
        cluster_for("cluster-b", (candidate_c,)),
    )
    verdicts = (
        VerdictDecision.merge(
            cluster_ids=("cluster-a", "cluster-b"),
            path="src/webhook.py",
            side="new",
            existing_code="def verify(): pass",
            title="Combined signature and latency issue",
            content="Unsigned path also exhibits slow verification.",
            recommendation="Verify signatures lazily with a cache.",
            category="authentication",
            severity=FindingSeverity.CRITICAL,
            primary_dimension="security",
            evidence_strength=EvidenceStrength.DIRECT,
            primary_location=SourceLocation("src/combined.py", 2, 2, "new", "e" * 64, False),
            changed_hunk_id="hunk-combined",
        ),
    )

    findings = FindingPublisher.build(
        task_id="review-1",
        candidates=(candidate_a, candidate_b, candidate_c),
        verdicts=verdicts,
        clusters=clusters,
    )

    assert len(findings) == 1
    finding = findings[0]
    # Union of reviewers across all clusters in the verdict, sorted and de-duplicated.
    assert finding.source_reviewer_references == (
        "general:v2",
        "performance:v2",
        "security:v2",
    )

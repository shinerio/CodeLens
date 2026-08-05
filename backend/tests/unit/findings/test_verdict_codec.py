"""VerdictCodec serialization round-trip tests.

Covers the regression where merge fields such as ``evidence_strength`` and
``primary_dimension`` must survive canonical_bytes -> decode without
being dropped or coerced to None.
"""

from codelens.findings.domain.candidates import EvidenceStrength
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.models import FindingSeverity
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome
from codelens.findings.infrastructure.verdict_codec import VerdictCodec


def _cluster(cluster_id: str) -> FindingCluster:
    return FindingCluster(
        cluster_id=cluster_id,
        candidate_ids=("cand-1",),
        canonical_candidate_id="cand-1",
        title="Stale cache entry",
        category="cache-invalidation",
        severity=FindingSeverity.HIGH,
        content="The invalidation leaves the old cache entry reachable.",
        recommendation="Remove the old entry during invalidation.",
        primary_dimension="correctness",
        evidence_strength=EvidenceStrength.DIRECT,
    )


def _merge_decision(cluster_id: str) -> VerdictDecision:
    return VerdictDecision.merge(
        cluster_ids=(cluster_id,),
        path="src/app.ts",
        side="new",
        existing_code="const x = 1;",
        title="Merged finding",
        content="Synthesized content.",
        recommendation="Fix it.",
        category="correctness",
        severity=FindingSeverity.MEDIUM,
        primary_dimension="correctness",
        evidence_strength=EvidenceStrength.DIRECT,
    )


def test_merge_decision_round_trips_through_canonical_bytes() -> None:
    """Merge decision must survive canonical_bytes -> decode unchanged."""
    cluster_id = "cluster_abc"
    codec = VerdictCodec(clusters=(_cluster(cluster_id),))
    decision = _merge_decision(cluster_id)

    payload = codec.canonical_bytes([decision])
    decoded = codec.decode(payload)

    assert len(decoded) == 1
    restored = decoded[0]
    assert restored.outcome is VerdictOutcome.MERGE
    assert restored.evidence_strength is EvidenceStrength.DIRECT
    assert restored.evidence_strength is not None
    assert restored.primary_dimension == "correctness"


def test_decode_decisions_preserves_merge_fields() -> None:
    """decode_decisions must return the same domain field values."""
    cluster_id = "cluster_xyz"
    codec = VerdictCodec(clusters=(_cluster(cluster_id),))
    decision = _merge_decision(cluster_id)

    decoded = codec.decode_decisions([decision])

    assert len(decoded) == 1
    restored = decoded[0]
    assert restored.evidence_strength is EvidenceStrength.DIRECT
    assert restored.evidence_strength is not None
    assert restored.primary_dimension == "correctness"

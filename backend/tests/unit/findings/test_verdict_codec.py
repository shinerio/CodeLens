"""VerdictCodec serialization round-trip tests.

Covers the regression where empty ``secondary_dimensions`` (``()``) was
serialized to ``null`` by a truthiness check, causing merge decisions to
fail validation on deserialize.
"""

from codelens.findings.domain.candidates import (
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
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
        secondary_dimensions=(),
        evidence_strength=EvidenceStrength.DIRECT,
        impact_certainty=ImpactCertainty.CONFIRMED,
        reproducibility=Reproducibility.DETERMINISTIC,
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
        secondary_dimensions=(),
        evidence_strength=EvidenceStrength.DIRECT,
        impact_certainty=ImpactCertainty.CONFIRMED,
        reproducibility=Reproducibility.DETERMINISTIC,
    )


def test_merge_decision_with_empty_secondary_dimensions_round_trips() -> None:
    """Empty secondary_dimensions tuple must survive canonical_bytes -> decode."""
    cluster_id = "cluster_abc"
    codec = VerdictCodec(clusters=(_cluster(cluster_id),))
    decision = _merge_decision(cluster_id)

    payload = codec.canonical_bytes([decision])
    decoded = codec.decode(payload)

    assert len(decoded) == 1
    restored = decoded[0]
    assert restored.outcome is VerdictOutcome.MERGE
    assert restored.secondary_dimensions == ()
    assert restored.secondary_dimensions is not None


def test_decode_decisions_preserves_empty_secondary_dimensions() -> None:
    """decode_decisions must not mutate empty tuple to None."""
    cluster_id = "cluster_xyz"
    codec = VerdictCodec(clusters=(_cluster(cluster_id),))
    decision = _merge_decision(cluster_id)

    decoded = codec.decode_decisions([decision])

    assert len(decoded) == 1
    assert decoded[0].secondary_dimensions == ()
    assert decoded[0].secondary_dimensions is not None

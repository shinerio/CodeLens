"""VerdictCodec serialization round-trip tests.

Covers the regression where merge fields such as ``evidence_strength`` and
``primary_dimension`` must survive canonical_bytes -> decode without
being dropped or coerced to None.
"""

import pytest

from codelens.findings.domain.candidates import EvidenceStrength
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome
from codelens.findings.infrastructure.verdict_codec import VerdictCodec, VerdictCodecError
from codelens.review.infrastructure.verdict_tools import VerdictSubmissionCollector


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
        primary_location=SourceLocation("src/app.ts", 3, 3, "new", "d" * 64, False),
        changed_hunk_id="hunk-merged",
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


async def test_verdict_collector_expands_batch_and_rejects_unknown_or_reused_clusters() -> None:
    collector = VerdictSubmissionCollector(
        VerdictCodec(clusters=(_cluster("cluster_a"), _cluster("cluster_b")))
    )

    await collector.verdict(["cluster_a", "cluster_b"], "accept")

    with pytest.raises(VerdictCodecError, match="already has a verdict"):
        await collector.verdict(["cluster_a"], "deny")
    with pytest.raises(VerdictCodecError, match="unknown cluster"):
        await collector.verdict(["cluster_unknown"], "deny")
    await collector.finalize()
    decisions = collector.final_output()
    assert [decision.cluster_ids for decision in decisions] == [
        ("cluster_a",),
        ("cluster_b",),
    ]
    assert all(decision.outcome is VerdictOutcome.ACCEPT for decision in decisions)


async def test_verdict_collector_rejects_duplicate_ids_within_one_call() -> None:
    collector = VerdictSubmissionCollector(VerdictCodec(clusters=(_cluster("cluster_a"),)))

    with pytest.raises(VerdictCodecError, match="duplicate"):
        await collector.verdict(["cluster_a", "cluster_a"], "accept")


def test_verdict_and_merge_tool_schemas_expose_only_v2_fields() -> None:
    collector = VerdictSubmissionCollector(VerdictCodec(clusters=(_cluster("cluster_a"),)))

    verdict_schema = collector.as_verdict_tool("verdict").params_json_schema
    merge_schema = collector.as_merge_tool("merge").params_json_schema

    assert set(verdict_schema["properties"]) == {"cluster_ids", "action"}
    assert verdict_schema["additionalProperties"] is False
    assert set(verdict_schema["required"]) == {"cluster_ids", "action"}
    assert set(merge_schema["properties"]) == {
        "cluster_ids",
        "path",
        "side",
        "existing_code",
        "title",
        "content",
        "recommendation",
        "category",
        "severity",
        "primary_dimension",
        "evidence_strength",
    }
    assert merge_schema["additionalProperties"] is False
    assert set(merge_schema["required"]) == set(merge_schema["properties"])

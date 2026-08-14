import json

from codelens.findings.domain.candidates import EvidenceStrength
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.models import FindingSeverity
from codelens.findings.infrastructure.verdict_codec import VerdictCodec
from codelens.review.infrastructure.verdict_tools import VerdictSubmissionCollector


def _cluster(cluster_id: str) -> FindingCluster:
    candidate_id = f"candidate_{cluster_id}"
    return FindingCluster(
        cluster_id=cluster_id,
        candidate_ids=(candidate_id,),
        canonical_candidate_id=candidate_id,
        category="correctness",
        title="Finding",
        severity=FindingSeverity.HIGH,
        primary_dimension="correctness",
        evidence_strength=EvidenceStrength.DIRECT,
        content="Content",
        recommendation="Recommendation",
    )


def _collector() -> VerdictSubmissionCollector:
    return VerdictSubmissionCollector(
        VerdictCodec(clusters=(_cluster("cluster_a"), _cluster("cluster_b")))
    )


async def test_verdict_partially_accepts_mixed_batch_and_rejects_overwrite() -> None:
    collector = _collector()

    result = json.loads(
        await collector.verdict(["cluster_a", "cluster_unknown", "cluster_a"], "accept")
    )
    overwrite = json.loads(await collector.verdict(["cluster_a"], "deny"))

    assert result["status"] == "partial"
    assert result["data"]["accepted_cluster_ids"] == ["cluster_a"]
    assert [item["code"] for item in result["data"]["rejected_clusters"]] == [
        "unknown_cluster",
        "duplicate_cluster_verdict",
    ]
    assert overwrite["status"] == "rejected"


async def test_finalize_needs_action_then_succeeds_and_freezes_state() -> None:
    collector = _collector()
    await collector.verdict(["cluster_a"], "accept")

    incomplete = json.loads(await collector.finalize())
    await collector.verdict(["cluster_b"], "deny")
    complete = json.loads(await collector.finalize())
    late = json.loads(await collector.verdict(["cluster_b"], "accept"))
    repeated = json.loads(await collector.finalize())

    assert incomplete["status"] == "needs_action"
    assert incomplete["data"]["missing_cluster_ids"] == ["cluster_b"]
    assert complete["status"] == "success"
    assert collector.is_completed is True
    assert late["diagnostics"][0]["code"] == "verdicts_already_finalized"
    assert repeated["status"] == "rejected"


async def test_merge_without_location_resolver_is_structured_failure() -> None:
    collector = _collector()

    result = json.loads(
        await collector.merge(
            ["cluster_a"],
            "src/a.py",
            "new",
            "x = 1",
            "Title",
            "Content",
            "Recommendation",
            "correctness",
            "high",
            "correctness",
            "direct",
        )
    )

    assert result["status"] == "failed"
    assert result["diagnostics"][0]["code"] == "location_resolver_unavailable"

import json

import pytest

from codelens.findings.application.resolve_clusters import ResolutionService
from codelens.findings.domain.candidates import (
    CandidateFinding,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.findings.domain.resolution import FindingCluster, ResolutionOutcome
from codelens.findings.infrastructure.resolver_output import (
    ResolutionDecisionDto,
    ResolverOutputCodec,
    ResolverSubmissionDto,
)
from codelens.review.infrastructure.resolution_tools import ResolutionSubmissionCollector


def candidate(
    candidate_id: str,
    *,
    evidence_strength: EvidenceStrength = EvidenceStrength.DIRECT,
    impact_certainty: ImpactCertainty = ImpactCertainty.CONFIRMED,
) -> CandidateFinding:
    return CandidateFinding(
        task_id="review-1",
        candidate_id=candidate_id,
        run_id="run-private",
        snapshot_id="snapshot-1",
        reviewer_reference="security:v1",
        category="authentication",
        title="Missing signature check",
        severity=FindingSeverity.HIGH,
        primary_dimension="security",
        secondary_dimensions=(),
        evidence_strength=evidence_strength,
        impact_certainty=impact_certainty,
        reproducibility=Reproducibility.DETERMINISTIC,
        primary_location=SourceLocation(
            "src/webhook.py", 5, 5, "new", "a" * 64, False
        ),
        related_locations=(),
        changed_hunk_id="hunk-1",
        existing_code_hash="a" * 64,
        evidence_hashes=("a" * 64,),
        content="The changed path accepts unsigned requests.",
        recommendation="Verify the signature before parsing.",
        fingerprint="b" * 64,
    )


def test_direct_policy_suppresses_weak_or_unclear_candidates() -> None:
    weak = candidate("candidate-weak", evidence_strength=EvidenceStrength.INFERRED)
    unclear = candidate(
        "candidate-unclear", impact_certainty=ImpactCertainty.UNCLEAR
    )

    decisions = ResolutionService.direct_decisions((weak, unclear))

    assert [decision.outcome for decision in decisions] == [
        ResolutionOutcome.SUPPRESS,
        ResolutionOutcome.SUPPRESS,
    ]
    assert all(not decision.is_publishable for decision in decisions)


def test_resolver_projection_is_stable_and_omits_execution_order() -> None:
    candidates = (candidate("candidate-b"), candidate("candidate-a"))
    clusters = tuple(
        FindingCluster(f"cluster-{item.candidate_id[-1]}", (item.candidate_id,))
        for item in candidates
    )

    first = ResolutionService.resolver_input_payload(
        plan_hash="c" * 64, clusters=clusters, candidates=candidates
    )
    second = ResolutionService.resolver_input_payload(
        plan_hash="c" * 64,
        clusters=tuple(reversed(clusters)),
        candidates=tuple(reversed(candidates)),
    )

    assert json.loads(first)["candidates"] == json.loads(second)["candidates"]
    assert b"run-private" not in first
    assert b"reviewer_reference" not in first


async def test_resolver_collector_accepts_only_one_complete_submission() -> None:
    source = candidate("candidate-a")
    cluster = FindingCluster("cluster-a", (source.candidate_id,))
    collector = ResolutionSubmissionCollector(ResolverOutputCodec((cluster,), (source,)))
    submission = ResolverSubmissionDto(
        schema_version="1",
        decisions=[
            ResolutionDecisionDto(
                cluster_id=cluster.cluster_id,
                outcome="suppress",
                canonical_candidate_id=None,
                merged_candidate_ids=[],
                severity=None,
                title=None,
                content=None,
                recommendation=None,
                reason_code="insufficient-evidence",
            )
        ],
    )

    await collector.submit(submission)
    with pytest.raises(ValueError, match="only once"):
        await collector.submit(submission)

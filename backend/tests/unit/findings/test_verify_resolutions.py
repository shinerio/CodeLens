from codelens.findings.application.verify_resolutions import (
    VerificationPolicy,
    VerificationService,
)
from codelens.findings.domain.resolution import (
    FindingCluster,
    ResolutionDecision,
)
from codelens.findings.infrastructure.verifier_output import VerifierOutputCodec


def verify_decision(cluster_id: str) -> ResolutionDecision:
    cluster = FindingCluster(cluster_id, ("candidate-a",))
    return ResolutionDecision.verify(
        cluster=cluster,
        canonical_candidate_id="candidate-a",
        merged_candidate_ids=("candidate-a",),
    )


async def test_unresolved_verification_is_suppressed() -> None:
    decision = verify_decision("cluster-a")
    selected = VerificationPolicy.select((decision,))
    service = VerificationService(VerifierOutputCodec(("cluster-a",)))

    result = await service.verify(
        {
            "schema_version": "1",
            "decisions": [
                {
                    "cluster_id": "cluster-a",
                    "outcome": "unresolved",
                    "reason": "insufficient-context",
                }
            ],
        }
    )

    assert selected == (decision,)
    assert result.published_cluster_ids == ()
    assert result.suppressed_cluster_ids == ("cluster-a",)

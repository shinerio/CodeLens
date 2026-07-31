import pytest

from codelens.findings.domain.resolution import (
    FindingCluster,
    ResolutionDecision,
    ResolutionOutcome,
    VerificationDecision,
    VerificationOutcome,
)


def test_resolution_decision_cannot_reference_unknown_candidate() -> None:
    cluster = FindingCluster(cluster_id="cluster-1", candidate_ids=("candidate-1",))

    with pytest.raises(ValueError, match="unknown candidate"):
        ResolutionDecision.publish(
            cluster=cluster,
            canonical_candidate_id="candidate-2",
            merged_candidate_ids=("candidate-2",),
        )


def test_publish_is_limited_to_candidates_in_the_cluster() -> None:
    cluster = FindingCluster(
        cluster_id="cluster-1",
        candidate_ids=("candidate-1", "candidate-2"),
    )

    decision = ResolutionDecision.publish(
        cluster=cluster,
        canonical_candidate_id="candidate-1",
        merged_candidate_ids=("candidate-2", "candidate-1"),
    )

    assert decision.outcome is ResolutionOutcome.PUBLISH
    assert decision.canonical_candidate_id == "candidate-1"
    assert decision.merged_candidate_ids == ("candidate-1", "candidate-2")


def test_cluster_rejects_empty_or_duplicate_candidate_sets() -> None:
    with pytest.raises(ValueError, match="at least one candidate"):
        FindingCluster(cluster_id="cluster-1", candidate_ids=())
    with pytest.raises(ValueError, match="duplicate candidates"):
        FindingCluster(
            cluster_id="cluster-1",
            candidate_ids=("candidate-1", "candidate-1"),
        )


def test_suppress_and_verify_preserve_the_cluster_boundary() -> None:
    cluster = FindingCluster(cluster_id="cluster-1", candidate_ids=("candidate-1",))

    suppressed = ResolutionDecision.suppress(cluster=cluster)
    verification = ResolutionDecision.verify(
        cluster=cluster,
        canonical_candidate_id="candidate-1",
        merged_candidate_ids=("candidate-1",),
    )

    assert suppressed.outcome is ResolutionOutcome.SUPPRESS
    assert suppressed.is_publishable is False
    assert verification.outcome is ResolutionOutcome.VERIFY
    assert verification.is_publishable is False


@pytest.mark.parametrize(
    ("factory_name", "outcome", "is_publishable"),
    [
        ("confirmed", VerificationOutcome.CONFIRMED, True),
        ("rejected", VerificationOutcome.REJECTED, False),
        ("unresolved", VerificationOutcome.UNRESOLVED, False),
    ],
)
def test_verification_outcomes_are_bounded_and_only_confirmed_publishes(
    factory_name: str,
    outcome: VerificationOutcome,
    is_publishable: bool,
) -> None:
    factory = getattr(VerificationDecision, factory_name)
    decision = factory(
        target_id="cluster-1",
        batch_target_ids=("cluster-1", "cluster-2"),
    )

    assert decision.outcome is outcome
    assert decision.is_publishable is is_publishable


def test_verification_cannot_decide_a_target_outside_its_batch() -> None:
    with pytest.raises(ValueError, match="outside the verification batch"):
        VerificationDecision.confirmed(
            target_id="cluster-2",
            batch_target_ids=("cluster-1",),
        )


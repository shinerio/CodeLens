import pytest

from codelens.findings.domain.resolution import VerificationOutcome
from codelens.findings.infrastructure.verifier_output import (
    VerificationValidationError,
    VerifierOutputCodec,
)


def test_verifier_rejects_unknown_cluster_and_extra_fields() -> None:
    codec = VerifierOutputCodec(("cluster-a",))

    with pytest.raises(VerificationValidationError, match="exactly once"):
        codec.decode(
            {
                "schema_version": "1",
                "decisions": [
                    {
                        "cluster_id": "cluster-unknown",
                        "outcome": "confirmed",
                        "reason": "reproduced",
                    }
                ],
            }
        )
    with pytest.raises(VerificationValidationError, match="schema"):
        codec.decode(
            {
                "schema_version": "1",
                "decisions": [
                    {
                        "cluster_id": "cluster-a",
                        "outcome": "confirmed",
                        "reason": "reproduced",
                        "severity": "critical",
                    }
                ],
            }
        )


def test_verifier_requires_one_decision_per_cluster() -> None:
    codec = VerifierOutputCodec(("cluster-a", "cluster-b"))

    decisions = codec.decode(
        {
            "schema_version": "1",
            "decisions": [
                {
                    "cluster_id": "cluster-b",
                    "outcome": "unresolved",
                    "reason": "insufficient-context",
                },
                {
                    "cluster_id": "cluster-a",
                    "outcome": "confirmed",
                    "reason": "reproduced",
                },
            ],
        }
    )

    assert [item.outcome for item in decisions] == [
        VerificationOutcome.UNRESOLVED,
        VerificationOutcome.CONFIRMED,
    ]

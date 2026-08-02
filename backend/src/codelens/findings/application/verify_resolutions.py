from dataclasses import dataclass

from codelens.findings.domain.resolution import (
    ResolutionDecision,
    ResolutionOutcome,
    VerificationDecision,
    VerificationOutcome,
)
from codelens.findings.infrastructure.verifier_output import VerifierOutputCodec


class VerificationPolicy:
    """Select only Resolver decisions that explicitly require bounded verification."""

    @staticmethod
    def select(
        decisions: tuple[ResolutionDecision, ...],
    ) -> tuple[ResolutionDecision, ...]:
        return tuple(
            decision
            for decision in decisions
            if decision.outcome is ResolutionOutcome.VERIFY
        )


@dataclass(frozen=True)
class VerificationResult:
    published_cluster_ids: tuple[str, ...]
    suppressed_cluster_ids: tuple[str, ...]
    decisions: tuple[VerificationDecision, ...]


class VerificationService:
    """Validate one complete Verifier batch and reduce it to publication eligibility."""

    def __init__(self, codec: VerifierOutputCodec) -> None:
        self._codec = codec

    async def verify(self, output: object) -> VerificationResult:
        decisions = self._codec.decode(output)
        return VerificationResult(
            published_cluster_ids=tuple(
                item.target_id
                for item in decisions
                if item.outcome is VerificationOutcome.CONFIRMED
            ),
            suppressed_cluster_ids=tuple(
                item.target_id
                for item in decisions
                if item.outcome
                in {VerificationOutcome.REJECTED, VerificationOutcome.UNRESOLVED}
            ),
            decisions=decisions,
        )

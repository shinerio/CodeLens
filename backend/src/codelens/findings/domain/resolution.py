from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class FindingCluster:
    """Group validated Candidate IDs that may describe one shared root cause."""

    cluster_id: str
    candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.cluster_id:
            raise ValueError("Finding cluster identifier cannot be empty")
        if not self.candidate_ids:
            raise ValueError("Finding cluster requires at least one candidate")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("Finding cluster contains duplicate candidates")


class ResolutionOutcome(StrEnum):
    """Bound Resolver output to publication, suppression, or verification."""

    PUBLISH = "publish"
    SUPPRESS = "suppress"
    VERIFY = "verify"


@dataclass(frozen=True)
class ResolutionDecision:
    """Record a no-invention decision over candidates from exactly one cluster."""

    cluster_id: str
    outcome: ResolutionOutcome
    canonical_candidate_id: str | None
    merged_candidate_ids: tuple[str, ...]

    @property
    def is_publishable(self) -> bool:
        """Return whether this decision can enter the published Finding pipeline."""

        return self.outcome is ResolutionOutcome.PUBLISH

    @classmethod
    def publish(
        cls,
        *,
        cluster: FindingCluster,
        canonical_candidate_id: str,
        merged_candidate_ids: tuple[str, ...],
    ) -> "ResolutionDecision":
        """Publish a canonical candidate supported only by its source cluster."""

        return cls._from_candidates(
            cluster=cluster,
            outcome=ResolutionOutcome.PUBLISH,
            canonical_candidate_id=canonical_candidate_id,
            merged_candidate_ids=merged_candidate_ids,
        )

    @classmethod
    def suppress(cls, *, cluster: FindingCluster) -> "ResolutionDecision":
        """Suppress a cluster without manufacturing a canonical candidate."""

        return cls(cluster.cluster_id, ResolutionOutcome.SUPPRESS, None, ())

    @classmethod
    def verify(
        cls,
        *,
        cluster: FindingCluster,
        canonical_candidate_id: str,
        merged_candidate_ids: tuple[str, ...],
    ) -> "ResolutionDecision":
        """Request bounded verification while retaining only cluster candidates."""

        return cls._from_candidates(
            cluster=cluster,
            outcome=ResolutionOutcome.VERIFY,
            canonical_candidate_id=canonical_candidate_id,
            merged_candidate_ids=merged_candidate_ids,
        )

    @classmethod
    def _from_candidates(
        cls,
        *,
        cluster: FindingCluster,
        outcome: ResolutionOutcome,
        canonical_candidate_id: str,
        merged_candidate_ids: tuple[str, ...],
    ) -> "ResolutionDecision":
        if not merged_candidate_ids:
            raise ValueError("Resolution requires at least one merged candidate")
        if len(merged_candidate_ids) != len(set(merged_candidate_ids)):
            raise ValueError("Resolution contains duplicate candidates")
        known_candidates = set(cluster.candidate_ids)
        if canonical_candidate_id not in known_candidates or any(
            candidate_id not in known_candidates
            for candidate_id in merged_candidate_ids
        ):
            raise ValueError("Resolution references an unknown candidate")
        if canonical_candidate_id not in merged_candidate_ids:
            raise ValueError("Canonical candidate must be included in the merge")
        return cls(
            cluster_id=cluster.cluster_id,
            outcome=outcome,
            canonical_candidate_id=canonical_candidate_id,
            merged_candidate_ids=tuple(sorted(merged_candidate_ids)),
        )


class VerificationOutcome(StrEnum):
    """Stable terminal outcomes emitted by the bounded Verifier pass."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class VerificationDecision:
    """Decide only a Candidate or Cluster identity present in one verifier batch."""

    target_id: str
    outcome: VerificationOutcome

    @property
    def is_publishable(self) -> bool:
        """Publish only an explicitly confirmed target."""

        return self.outcome is VerificationOutcome.CONFIRMED

    @classmethod
    def confirmed(
        cls, *, target_id: str, batch_target_ids: tuple[str, ...]
    ) -> "VerificationDecision":
        """Confirm one target from the supplied verifier batch."""

        return cls._create(target_id, VerificationOutcome.CONFIRMED, batch_target_ids)

    @classmethod
    def rejected(
        cls, *, target_id: str, batch_target_ids: tuple[str, ...]
    ) -> "VerificationDecision":
        """Reject one target from the supplied verifier batch."""

        return cls._create(target_id, VerificationOutcome.REJECTED, batch_target_ids)

    @classmethod
    def unresolved(
        cls, *, target_id: str, batch_target_ids: tuple[str, ...]
    ) -> "VerificationDecision":
        """Suppress an unresolved target from the supplied verifier batch."""

        return cls._create(target_id, VerificationOutcome.UNRESOLVED, batch_target_ids)

    @classmethod
    def _create(
        cls,
        target_id: str,
        outcome: VerificationOutcome,
        batch_target_ids: tuple[str, ...],
    ) -> "VerificationDecision":
        if target_id not in batch_target_ids:
            raise ValueError("Verification target is outside the verification batch")
        return cls(target_id=target_id, outcome=outcome)


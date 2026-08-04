"""Final Verifier decision model for the simplified two-stage Review DAG."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from codelens.findings.domain.candidates import (
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.models import FindingSeverity


class VerdictOutcome(StrEnum):
    """Terminal outcomes emitted by the Final Verifier."""

    ACCEPT = "accept"
    DENY = "deny"


@dataclass(frozen=True)
class VerdictDecision:
    """Record a Final Verifier decision over one or more clusters.

    The Final Verifier can:
    - Accept or deny individual clusters
    - Merge multiple clusters into a single Finding
    - Override/merge any attribute from the original CandidateFinding

    When verdict is DENY, all merge fields are ignored.
    When verdict is ACCEPT, provided fields override the canonical candidate's values;
    None fields preserve the original values.
    """

    cluster_ids: tuple[str, ...]
    outcome: VerdictOutcome

    # Optional merge fields (only used when outcome is ACCEPT)
    path: str | None = None
    side: Literal["old", "new"] | None = None
    existing_code: str | None = None
    title: str | None = None
    content: str | None = None
    recommendation: str | None = None
    category: str | None = None
    severity: FindingSeverity | None = None
    primary_dimension: str | None = None
    secondary_dimensions: tuple[str, ...] | None = None
    evidence_strength: EvidenceStrength | None = None
    impact_certainty: ImpactCertainty | None = None
    reproducibility: Reproducibility | None = None

    def __post_init__(self) -> None:
        if not self.cluster_ids:
            raise ValueError("VerdictDecision requires at least one cluster_id")
        if len(self.cluster_ids) != len(set(self.cluster_ids)):
            raise ValueError("VerdictDecision contains duplicate cluster_ids")

    @property
    def is_publishable(self) -> bool:
        """Return whether this decision should be published as a Finding."""
        return self.outcome is VerdictOutcome.ACCEPT

    @classmethod
    def accept(
        cls,
        *,
        cluster_ids: tuple[str, ...],
        path: str | None = None,
        side: Literal["old", "new"] | None = None,
        existing_code: str | None = None,
        title: str | None = None,
        content: str | None = None,
        recommendation: str | None = None,
        category: str | None = None,
        severity: FindingSeverity | None = None,
        primary_dimension: str | None = None,
        secondary_dimensions: tuple[str, ...] | None = None,
        evidence_strength: EvidenceStrength | None = None,
        impact_certainty: ImpactCertainty | None = None,
        reproducibility: Reproducibility | None = None,
    ) -> "VerdictDecision":
        """Accept one or more clusters, optionally overriding their attributes."""
        return cls(
            cluster_ids=cluster_ids,
            outcome=VerdictOutcome.ACCEPT,
            path=path,
            side=side,
            existing_code=existing_code,
            title=title,
            content=content,
            recommendation=recommendation,
            category=category,
            severity=severity,
            primary_dimension=primary_dimension,
            secondary_dimensions=secondary_dimensions,
            evidence_strength=evidence_strength,
            impact_certainty=impact_certainty,
            reproducibility=reproducibility,
        )

    @classmethod
    def deny(cls, *, cluster_ids: tuple[str, ...]) -> "VerdictDecision":
        """Deny one or more clusters (suppress from publication)."""
        return cls(cluster_ids=cluster_ids, outcome=VerdictOutcome.DENY)

"""Final Verifier decision model for the simplified two-stage Review DAG.

The Final Verifier judges each ``FindingCluster`` via three terminal outcomes:

- ``ACCEPT``: publish the cluster as-is using its canonical candidate fields.
- ``DENY``: suppress the cluster (false-positive filtering).
- ``MERGE``: publish a synthesized Finding whose fields are all supplied by
  the model, merging multiple candidates/clusters into one Finding.

Every cluster must be covered by exactly one verdict decision before the
Final Verifier can finalize.
"""

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
    MERGE = "merge"


@dataclass(frozen=True)
class VerdictDecision:
    """Record a Final Verifier decision over one or more clusters.

    - ``accept``: publish using the cluster's canonical candidate fields;
      merge fields are ignored.
    - ``deny``: suppress the cluster; merge fields are ignored.
    - ``merge``: publish a synthesized Finding; all merge fields are required
      and override the canonical candidate's values.
    """

    cluster_ids: tuple[str, ...]
    outcome: VerdictOutcome

    # Merge fields (required when outcome is MERGE, ignored otherwise)
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
        if self.outcome is VerdictOutcome.MERGE:
            missing = self._missing_merge_fields()
            if missing:
                raise ValueError(
                    f"VerdictDecision merge requires all fields; missing: {missing}"
                )

    def _missing_merge_fields(self) -> tuple[str, ...]:
        names = (
            "path",
            "side",
            "existing_code",
            "title",
            "content",
            "recommendation",
            "category",
            "severity",
            "primary_dimension",
            "secondary_dimensions",
            "evidence_strength",
            "impact_certainty",
            "reproducibility",
        )
        return tuple(name for name in names if getattr(self, name) is None)

    @property
    def is_publishable(self) -> bool:
        """Return whether this decision should be published as a Finding."""
        return self.outcome is not VerdictOutcome.DENY

    @classmethod
    def accept(cls, *, cluster_ids: tuple[str, ...]) -> "VerdictDecision":
        """Accept one or more clusters as-is, using their canonical fields."""
        return cls(cluster_ids=cluster_ids, outcome=VerdictOutcome.ACCEPT)

    @classmethod
    def deny(cls, *, cluster_ids: tuple[str, ...]) -> "VerdictDecision":
        """Deny one or more clusters (suppress from publication)."""
        return cls(cluster_ids=cluster_ids, outcome=VerdictOutcome.DENY)

    @classmethod
    def merge(
        cls,
        *,
        cluster_ids: tuple[str, ...],
        path: str,
        side: Literal["old", "new"],
        existing_code: str,
        title: str,
        content: str,
        recommendation: str,
        category: str,
        severity: FindingSeverity,
        primary_dimension: str,
        secondary_dimensions: tuple[str, ...],
        evidence_strength: EvidenceStrength,
        impact_certainty: ImpactCertainty,
        reproducibility: Reproducibility,
    ) -> "VerdictDecision":
        """Merge one or more clusters into a single Finding.

        All merge fields are required; the model synthesizes the final
        Finding attributes across the merged candidates.
        """
        return cls(
            cluster_ids=cluster_ids,
            outcome=VerdictOutcome.MERGE,
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

"""Cluster validated Candidate IDs that may describe one shared root cause.

A ``FindingCluster`` groups ``CandidateFinding`` objects produced by different
Reviewers that share the same location, category, title, and evidence hashes.
The Final Verifier judges each cluster as a whole via the verdict/merge tools.
"""

from dataclasses import dataclass

from codelens.findings.domain.candidates import EvidenceStrength
from codelens.findings.domain.models import FindingSeverity


@dataclass(frozen=True)
class FindingCluster:
    """Group validated Candidate IDs that may describe one shared root cause.

    The ``canonical_candidate_id`` is the deterministic representative
    (lexicographically first ``candidate_id``) whose fields are copied onto the
    cluster for the Final Verifier context and for the ``accept`` verdict path.
    When the Verifier returns ``merge``, it supplies synthesized fields that
    override these canonical values.
    """

    cluster_id: str
    candidate_ids: tuple[str, ...]
    canonical_candidate_id: str
    title: str
    category: str
    severity: FindingSeverity
    content: str
    recommendation: str
    primary_dimension: str
    evidence_strength: EvidenceStrength

    def __post_init__(self) -> None:
        if not self.cluster_id:
            raise ValueError("Finding cluster identifier cannot be empty")
        if not self.candidate_ids:
            raise ValueError("Finding cluster requires at least one candidate")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("Finding cluster contains duplicate candidates")
        if self.canonical_candidate_id not in self.candidate_ids:
            raise ValueError(
                "Finding cluster canonical candidate must be a member of the cluster"
            )

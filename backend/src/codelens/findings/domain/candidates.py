from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from codelens.findings.domain.models import FindingSeverity, SourceLocation


class EvidenceStrength(StrEnum):
    """Classify how directly frozen evidence establishes a candidate."""

    DIRECT = "direct"
    INFERRED = "inferred"
    WEAK = "weak"


@dataclass(frozen=True)
class CandidateFinding:
    """Hold one Snapshot-resolved Comment v2 candidate before publication.

    Every identity and evidence hash is host-derived. Numeric confidence is
    deliberately absent; downstream resolution uses the categorical evidence
    axes without trusting model-calibrated probabilities.
    """

    task_id: str
    candidate_id: str
    run_id: str
    snapshot_id: str
    reviewer_reference: str
    category: str
    title: str
    severity: FindingSeverity
    primary_dimension: str
    evidence_strength: EvidenceStrength
    primary_location: SourceLocation
    related_locations: tuple[SourceLocation, ...]
    changed_hunk_id: str | None
    existing_code_hash: str
    evidence_hashes: tuple[str, ...]
    content: str
    recommendation: str
    fingerprint: str


@dataclass(frozen=True)
class CandidateFindingBatch:
    """Group resolved Comment v2 candidates under their immutable schema version."""

    candidates: tuple[CandidateFinding, ...]
    schema_version: Literal["2"] = "2"

    def __post_init__(self) -> None:
        identities = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(identities) != len(set(identities)):
            raise ValueError("CandidateFindingBatch contains duplicate candidate IDs")


"""Validate and canonicalize Final Verifier decisions against frozen clusters."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from codelens.findings.domain.candidates import EvidenceStrength
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.models import FindingSeverity
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome

_ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=240),
]
_LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
]
_Path = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
_ClusterID = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^cluster_[a-z0-9_-]+$",
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerdictDecisionDto(_StrictModel):
    """Validate one Final Verifier decision over one or more clusters."""

    cluster_ids: Annotated[list[_ClusterID], Field(min_length=1, max_length=64)]
    outcome: Literal["accept", "deny", "merge"]

    # Merge fields: required when outcome is "merge", ignored for accept/deny.
    # The VerdictDecision domain constructor enforces merge completeness.
    path: _Path | None = None
    side: Literal["old", "new"] | None = None
    existing_code: _LongText | None = None
    title: _ShortText | None = None
    content: _LongText | None = None
    recommendation: _LongText | None = None
    category: _ShortText | None = None
    severity: Literal["critical", "high", "medium", "low", "info"] | None = None
    primary_dimension: _ShortText | None = None
    evidence_strength: Literal["direct", "inferred", "weak"] | None = None


class VerdictSubmissionDto(_StrictModel):
    """Version the strict model-facing Verdict submission envelope."""

    schema_version: Literal["1"]
    decisions: Annotated[list[VerdictDecisionDto], Field(max_length=64)]


class VerdictCodecError(ValueError):
    """Reject malformed or incorrectly versioned Verdict output."""


@dataclass(frozen=True)
class ValidatedVerdictBatch:
    """Carry a complete Final Verifier decision set into atomic checkpoint completion."""

    decisions: tuple[VerdictDecision, ...]


@dataclass(frozen=True)
class VerdictCodec:
    """Validate and canonicalize Final Verifier decisions against frozen clusters."""

    schema_version: Literal["1"] = "1"
    clusters: tuple[FindingCluster, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise VerdictCodecError("unsupported Verdict schema version")
        cluster_ids = [c.cluster_id for c in self.clusters]
        if len(cluster_ids) != len(set(cluster_ids)):
            raise VerdictCodecError("VerdictCodec received duplicate clusters")

    def decode(self, payload: object) -> tuple[VerdictDecision, ...]:
        """Validate untrusted Verdict output and return domain decisions."""
        try:
            if isinstance(payload, VerdictSubmissionDto):
                submission = payload
            elif isinstance(payload, bytes | str):
                submission = VerdictSubmissionDto.model_validate_json(payload)
            elif isinstance(payload, Mapping):
                submission = VerdictSubmissionDto.model_validate(dict(payload))
            else:
                raise VerdictCodecError("Verdict output must be a JSON object")
        except ValidationError as error:
            raise VerdictCodecError("Verdict output schema is invalid") from error

        # Validate cluster coverage: every cluster must be covered exactly once
        known_cluster_ids = {c.cluster_id for c in self.clusters}
        covered_cluster_ids: set[str] = set()
        for decision in submission.decisions:
            for cluster_id in decision.cluster_ids:
                if cluster_id not in known_cluster_ids:
                    raise VerdictCodecError(
                        f"Verdict references unknown cluster: {cluster_id}"
                    )
                if cluster_id in covered_cluster_ids:
                    raise VerdictCodecError(
                        f"Cluster {cluster_id} is covered by multiple verdicts"
                    )
                covered_cluster_ids.add(cluster_id)

        if covered_cluster_ids != known_cluster_ids:
            missing = known_cluster_ids - covered_cluster_ids
            raise VerdictCodecError(
                f"Verdict does not cover all clusters. Missing: {sorted(missing)}"
            )

        return tuple(self._to_domain(d) for d in submission.decisions)

    def decode_decisions(
        self, decisions: Sequence[VerdictDecision]
    ) -> tuple[VerdictDecision, ...]:
        """Validate a list of VerdictDecision objects directly (not through DTO).

        This is used by the finalize flow where we accumulate domain objects
        rather than DTOs.
        """
        # Validate cluster coverage: every cluster must be covered exactly once
        known_cluster_ids = {c.cluster_id for c in self.clusters}
        covered_cluster_ids: set[str] = set()
        for decision in decisions:
            for cluster_id in decision.cluster_ids:
                if cluster_id not in known_cluster_ids:
                    raise VerdictCodecError(
                        f"Verdict references unknown cluster: {cluster_id}"
                    )
                if cluster_id in covered_cluster_ids:
                    raise VerdictCodecError(
                        f"Cluster {cluster_id} is covered by multiple verdicts"
                    )
                covered_cluster_ids.add(cluster_id)

        if covered_cluster_ids != known_cluster_ids:
            missing = known_cluster_ids - covered_cluster_ids
            raise VerdictCodecError(
                f"Verdict does not cover all clusters. Missing: {sorted(missing)}"
            )

        return tuple(decisions)

    def canonical_bytes(self, decisions: Sequence[VerdictDecision]) -> bytes:
        """Canonicalize validated Verdict decisions to deterministic JSON."""
        payload = {
            "schema_version": self.schema_version,
            "decisions": [self._to_dto(d) for d in decisions],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _to_domain(self, dto: VerdictDecisionDto) -> VerdictDecision:
        """Convert a validated DTO to a domain VerdictDecision."""
        return VerdictDecision(
            cluster_ids=tuple(dto.cluster_ids),
            outcome=VerdictOutcome(dto.outcome),
            path=dto.path,
            side=dto.side,
            existing_code=dto.existing_code,
            title=dto.title,
            content=dto.content,
            recommendation=dto.recommendation,
            category=dto.category,
            severity=FindingSeverity(dto.severity) if dto.severity else None,
            primary_dimension=dto.primary_dimension,
            evidence_strength=(
                EvidenceStrength(dto.evidence_strength) if dto.evidence_strength else None
            ),
        )

    def _to_dto(self, decision: VerdictDecision) -> dict[str, Any]:
        """Convert a domain VerdictDecision to a JSON-serializable dict."""
        return {
            "cluster_ids": list(decision.cluster_ids),
            "outcome": decision.outcome.value,
            "path": decision.path,
            "side": decision.side,
            "existing_code": decision.existing_code,
            "title": decision.title,
            "content": decision.content,
            "recommendation": decision.recommendation,
            "category": decision.category,
            "severity": decision.severity.value if decision.severity else None,
            "primary_dimension": decision.primary_dimension,
            "evidence_strength": (
                decision.evidence_strength.value if decision.evidence_strength else None
            ),
        }

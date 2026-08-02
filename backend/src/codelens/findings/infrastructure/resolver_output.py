import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from codelens.findings.domain.models import FindingSeverity
from codelens.findings.domain.resolution import (
    FindingCluster,
    ResolutionDecision,
)

_ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=240),
]
_LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
]
_Identity = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_-]*$",
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResolutionDecisionDto(_StrictModel):
    cluster_id: _Identity
    outcome: Literal["publish", "suppress", "verify"]
    canonical_candidate_id: _Identity | None
    merged_candidate_ids: Annotated[list[_Identity], Field(max_length=32)]
    severity: Literal["critical", "high", "medium", "low", "info"] | None
    title: _ShortText | None
    content: _LongText | None
    recommendation: _LongText | None
    reason_code: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=128,
            pattern=r"^[a-z][a-z0-9_.-]*$",
        ),
    ]


class ResolverSubmissionDto(_StrictModel):
    schema_version: Literal["1"]
    decisions: Annotated[list[ResolutionDecisionDto], Field(max_length=64)]


class ResolutionValidationError(ValueError):
    """Reject Resolver output that invents identities, evidence, or severity."""


@dataclass(frozen=True)
class ValidatedResolutionBatch:
    """Carry a complete Resolver decision set into atomic checkpoint completion."""

    decisions: tuple[ResolutionDecision, ...]


_SEVERITY_RANK = {
    FindingSeverity.CRITICAL: 0,
    FindingSeverity.HIGH: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.LOW: 3,
    FindingSeverity.INFO: 4,
}


class ResolverCandidateConstraint(Protocol):
    """Expose only Candidate identity and severity needed by no-invention checks."""

    @property
    def candidate_id(self) -> str: ...

    @property
    def severity(self) -> FindingSeverity: ...


class ResolverOutputCodec:
    """Validate one complete bounded Resolver decision set against frozen clusters."""

    def __init__(
        self,
        clusters: tuple[FindingCluster, ...],
        candidates: tuple[ResolverCandidateConstraint, ...],
    ) -> None:
        self._clusters = {cluster.cluster_id: cluster for cluster in clusters}
        self._candidates = {
            candidate.candidate_id: candidate for candidate in candidates
        }
        known_members = {
            candidate_id for cluster in clusters for candidate_id in cluster.candidate_ids
        }
        if known_members != set(self._candidates):
            raise ValueError("Resolver clusters must partition the supplied Candidates")

    def decode(self, payload: object) -> tuple[ResolutionDecision, ...]:
        try:
            if isinstance(payload, ResolverSubmissionDto):
                submission = payload
            elif isinstance(payload, bytes | str):
                submission = ResolverSubmissionDto.model_validate_json(payload)
            elif isinstance(payload, Mapping):
                submission = ResolverSubmissionDto.model_validate(dict(payload))
            else:
                raise ResolutionValidationError("Resolver output must be a JSON object")
        except ValidationError as error:
            raise ResolutionValidationError("Resolver output schema is invalid") from error
        cluster_ids = tuple(item.cluster_id for item in submission.decisions)
        if len(cluster_ids) != len(set(cluster_ids)) or set(cluster_ids) != set(
            self._clusters
        ):
            raise ResolutionValidationError(
                "Resolver must decide exactly once for every cluster"
            )
        return tuple(self._decision(item) for item in submission.decisions)

    def validate(
        self, decisions: Sequence[ResolutionDecision]
    ) -> tuple[ResolutionDecision, ...]:
        """Validate already typed decisions with the same no-invention rules."""

        payload = ResolverSubmissionDto(
            schema_version="1",
            decisions=[
                ResolutionDecisionDto(
                    cluster_id=item.cluster_id,
                    outcome=item.outcome.value,
                    canonical_candidate_id=item.canonical_candidate_id,
                    merged_candidate_ids=list(item.merged_candidate_ids),
                    severity=item.severity.value if item.severity is not None else None,
                    title=item.title,
                    content=item.content,
                    recommendation=item.recommendation,
                    reason_code=item.reason_code or "unspecified",
                )
                for item in decisions
            ],
        )
        return self.decode(payload)

    def canonical_bytes(self, decisions: Sequence[ResolutionDecision]) -> bytes:
        validated = self.validate(decisions)
        payload = {
            "decisions": [
                {
                    "canonical_candidate_id": item.canonical_candidate_id,
                    "cluster_id": item.cluster_id,
                    "content": item.content,
                    "merged_candidate_ids": list(item.merged_candidate_ids),
                    "outcome": item.outcome.value,
                    "reason_code": item.reason_code,
                    "recommendation": item.recommendation,
                    "severity": item.severity.value if item.severity else None,
                    "title": item.title,
                }
                for item in validated
            ],
            "schema_version": "1",
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _decision(self, value: ResolutionDecisionDto) -> ResolutionDecision:
        cluster = self._clusters.get(value.cluster_id)
        if cluster is None:
            raise ResolutionValidationError("Resolver referenced an unknown cluster")
        if value.outcome == "suppress":
            if any(
                item is not None
                for item in (
                    value.canonical_candidate_id,
                    value.severity,
                    value.title,
                    value.content,
                    value.recommendation,
                )
            ) or value.merged_candidate_ids:
                raise ResolutionValidationError(
                    "suppressed resolution cannot publish Candidate content"
                )
            return ResolutionDecision.suppress(
                cluster=cluster, reason_code=value.reason_code
            )
        if (
            value.canonical_candidate_id is None
            or value.severity is None
            or value.title is None
            or value.content is None
            or value.recommendation is None
        ):
            raise ResolutionValidationError(
                "publish and verify resolutions require normalized content"
            )
        try:
            decision = (
                ResolutionDecision.publish
                if value.outcome == "publish"
                else ResolutionDecision.verify
            )(
                cluster=cluster,
                canonical_candidate_id=value.canonical_candidate_id,
                merged_candidate_ids=tuple(value.merged_candidate_ids),
                severity=FindingSeverity(value.severity),
                title=value.title,
                content=value.content,
                recommendation=value.recommendation,
                reason_code=value.reason_code,
            )
        except ValueError as error:
            raise ResolutionValidationError(
                "Resolver referenced an unknown candidate"
            ) from error
        candidate_severities = (
            self._candidates[candidate_id].severity
            for candidate_id in decision.merged_candidate_ids
        )
        maximum = min(candidate_severities, key=_SEVERITY_RANK.__getitem__)
        if decision.severity is None or _SEVERITY_RANK[decision.severity] < _SEVERITY_RANK[maximum]:
            raise ResolutionValidationError(
                "Resolver severity exceeds every merged Candidate"
            )
        return decision

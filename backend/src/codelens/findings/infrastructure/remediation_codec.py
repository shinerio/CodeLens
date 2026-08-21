"""Validate and canonicalize Remediator decisions against frozen pending findings.

The RemediationCodec mirrors :class:`~codelens.findings.infrastructure.dedup_codec.DedupCodec`
but operates on ``remediation_ref`` strings (``"{source_id}:{finding_id}"``) —
the stable identifier every pending existing finding receives when the role
context is prepared. Every pending finding must be covered by exactly one
remediation decision before the Remediator can finalize.

A two-layer filter applies upstream of the codec:

1. **Deterministic pre-filter** — findings whose source file was not touched
   by the current diff are auto-marked ``unresolved`` (see
   :func:`~codelens.findings.domain.remediation.run_deterministic_remediation_filter`).
   These decisions are persisted directly and removed from the pending set,
   so the codec only validates the LLM-judged remainder.
2. **LLM remediation** — the Remediator agent inspects the current code at
   each remaining finding's location, compares against the ``existing_code``
   anchor, and judges whether the issue is ``resolved``, ``unresolved``,
   or ``unclear`` using the ``resolved_review`` tool for batch marking and
   ``remediation_done`` as a coverage gate.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from codelens.findings.domain.remediation import (
    PendingRemediation,
    RemediationDecision,
    RemediationDecisionSource,
    RemediationOutcome,
)

_RemediationRef = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=512,
        pattern=r"^[a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+$",
    ),
]

_EvidenceSummary = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2048,
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RemediationDecisionDto(_StrictModel):
    """Validate one Remediator decision over a single pending existing finding."""

    remediation_ref: _RemediationRef
    outcome: Literal["resolved", "unresolved", "unclear"]
    evidence_summary: _EvidenceSummary


class RemediationSubmissionDto(_StrictModel):
    """Version the strict model-facing remediation submission envelope."""

    schema_version: Literal["1"]
    decisions: Annotated[list[RemediationDecisionDto], Field(max_length=500)]


class RemediationCodecError(ValueError):
    """Reject malformed or incorrectly versioned remediation output."""


@dataclass(frozen=True)
class ValidatedRemediationBatch:
    """Carry a complete Remediator decision set into atomic checkpoint completion."""

    decisions: tuple[RemediationDecision, ...]


@dataclass(frozen=True)
class RemediationCodec:
    """Validate and canonicalize Remediator decisions against frozen pending findings.

    ``expected_refs`` is the set of ``remediation_ref`` strings the Remediator
    must judge. Deterministic pre-filter results are removed from this set
    before the codec is constructed, so the codec only enforces coverage over
    the LLM-judged remainder.
    """

    expected_refs: frozenset[str]
    schema_version: Literal["1"] = "1"

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise RemediationCodecError("unsupported remediation schema version")

    @classmethod
    def from_pending(cls, pending: Sequence[PendingRemediation]) -> "RemediationCodec":
        """Build a codec keyed by the refs of findings the LLM must judge."""

        return cls(expected_refs=frozenset(item.remediation_ref for item in pending))

    def decode(self, payload: object) -> tuple[RemediationDecision, ...]:
        """Validate untrusted remediation output and return domain decisions."""

        try:
            if isinstance(payload, RemediationSubmissionDto):
                submission = payload
            elif isinstance(payload, bytes | str):
                submission = RemediationSubmissionDto.model_validate_json(payload)
            elif isinstance(payload, Mapping):
                submission = RemediationSubmissionDto.model_validate(dict(payload))
            else:
                raise RemediationCodecError("Remediation output must be a JSON object")
        except ValidationError as error:
            raise RemediationCodecError("Remediation output schema is invalid") from error

        self._validate_coverage(submission.decisions)
        return tuple(self._to_domain(dto) for dto in submission.decisions)

    def decode_decisions(
        self, decisions: Sequence[RemediationDecision]
    ) -> tuple[RemediationDecision, ...]:
        """Validate a list of RemediationDecision objects directly (not through DTO).

        This is used by the finalize flow where we accumulate domain objects
        rather than DTOs.
        """

        self._validate_coverage(decisions)
        return tuple(decisions)

    def validate_new_refs(
        self,
        remediation_refs: Sequence[str],
        covered_refs: set[str],
    ) -> tuple[str, ...]:
        """Validate one tool submission before mutating collector state."""

        normalized = tuple(remediation_refs)
        if not normalized:
            raise RemediationCodecError("Remediation requires at least one remediation_ref")
        if len(normalized) != len(set(normalized)):
            raise RemediationCodecError("Remediation contains duplicate remediation_refs")
        for remediation_ref in normalized:
            if remediation_ref not in self.expected_refs:
                raise RemediationCodecError(
                    f"Remediation references unknown pending finding: {remediation_ref}"
                )
            if remediation_ref in covered_refs:
                raise RemediationCodecError(
                    f"Pending finding {remediation_ref} already has a remediation decision"
                )
        return normalized

    def canonical_bytes(self, decisions: Sequence[RemediationDecision]) -> bytes:
        """Canonicalize validated remediation decisions to deterministic JSON."""

        payload = {
            "schema_version": self.schema_version,
            "decisions": [self._to_dto(d) for d in decisions],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _validate_coverage(
        self, decisions: Sequence[RemediationDecisionDto | RemediationDecision]
    ) -> None:
        covered_refs: set[str] = set()
        for decision in decisions:
            remediation_ref = self._ref_of(decision)
            if remediation_ref not in self.expected_refs:
                raise RemediationCodecError(
                    f"Remediation references unknown pending finding: {remediation_ref}"
                )
            if remediation_ref in covered_refs:
                raise RemediationCodecError(
                    f"Pending finding {remediation_ref} is covered by "
                    "multiple remediation decisions"
                )
            covered_refs.add(remediation_ref)
        if covered_refs != self.expected_refs:
            missing = sorted(self.expected_refs - covered_refs)
            raise RemediationCodecError(
                f"Remediation does not cover all pending findings. Missing: {missing}"
            )

    @staticmethod
    def _split_ref(remediation_ref: str) -> tuple[str, str]:
        """Split ``"{source_id}:{finding_id}"`` back into its components."""

        source_id, _, finding_id = remediation_ref.partition(":")
        return source_id, finding_id

    @staticmethod
    def _ref_of(
        decision: RemediationDecisionDto | RemediationDecision,
    ) -> str:
        """Extract the ``remediation_ref`` from a DTO or domain decision."""

        if isinstance(decision, RemediationDecision):
            return f"{decision.source_id}:{decision.finding_id}"
        return decision.remediation_ref

    @staticmethod
    def _to_domain(dto: RemediationDecisionDto) -> RemediationDecision:
        source_id, finding_id = RemediationCodec._split_ref(dto.remediation_ref)
        return RemediationDecision(
            source_id=source_id,
            finding_id=finding_id,
            outcome=RemediationOutcome(dto.outcome),
            evidence_summary=dto.evidence_summary,
            decision_source=RemediationDecisionSource.LLM,
        )

    @staticmethod
    def _to_dto(decision: RemediationDecision) -> dict[str, Any]:
        return {
            "remediation_ref": f"{decision.source_id}:{decision.finding_id}",
            "outcome": decision.outcome.value,
            "evidence_summary": decision.evidence_summary,
        }

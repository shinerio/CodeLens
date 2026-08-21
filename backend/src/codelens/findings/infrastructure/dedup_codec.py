"""Validate and canonicalize Deduplicator decisions against frozen survived findings.

The DedupCodec mirrors :class:`~codelens.findings.infrastructure.verdict_codec.VerdictCodec`
but operates on ``verdict_decision_id`` strings (the survived verdicts to judge)
instead of cluster IDs. Every survived finding must be covered by exactly one
dedup decision before the Deduplicator can finalize.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from codelens.findings.domain.dedup import DedupDecision, DedupDecisionSource, DedupOutcome

_VerdictDecisionId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^verdict_[a-z0-9]+$",
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DedupDecisionDto(_StrictModel):
    """Validate one Deduplicator decision over a single survived finding."""

    verdict_decision_id: _VerdictDecisionId
    outcome: Literal["accept", "deny"]


class DedupSubmissionDto(_StrictModel):
    """Version the strict model-facing Dedup submission envelope."""

    schema_version: Literal["1"]
    decisions: Annotated[list[DedupDecisionDto], Field(max_length=500)]


class DedupCodecError(ValueError):
    """Reject malformed or incorrectly versioned Dedup output."""


@dataclass(frozen=True)
class ValidatedDedupBatch:
    """Carry a complete Deduplicator decision set into atomic checkpoint completion."""

    decisions: tuple[DedupDecision, ...]


@dataclass(frozen=True)
class DedupCodec:
    """Validate and canonicalize Deduplicator decisions against frozen survived findings."""

    expected_ids: frozenset[str]
    schema_version: Literal["1"] = "1"

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise DedupCodecError("unsupported Dedup schema version")

    def decode(self, payload: object) -> tuple[DedupDecision, ...]:
        """Validate untrusted Dedup output and return domain decisions."""

        try:
            if isinstance(payload, DedupSubmissionDto):
                submission = payload
            elif isinstance(payload, bytes | str):
                submission = DedupSubmissionDto.model_validate_json(payload)
            elif isinstance(payload, Mapping):
                submission = DedupSubmissionDto.model_validate(dict(payload))
            else:
                raise DedupCodecError("Dedup output must be a JSON object")
        except ValidationError as error:
            raise DedupCodecError("Dedup output schema is invalid") from error

        self._validate_coverage(submission.decisions)
        return tuple(self._to_domain(dto) for dto in submission.decisions)

    def decode_decisions(self, decisions: Sequence[DedupDecision]) -> tuple[DedupDecision, ...]:
        """Validate a list of DedupDecision objects directly (not through DTO).

        This is used by the finalize flow where we accumulate domain objects
        rather than DTOs.
        """

        self._validate_coverage(decisions)
        return tuple(decisions)

    def validate_new_ids(
        self,
        verdict_decision_ids: Sequence[str],
        covered_ids: set[str],
    ) -> tuple[str, ...]:
        """Validate one tool submission before mutating collector state."""

        normalized = tuple(verdict_decision_ids)
        if not normalized:
            raise DedupCodecError("Dedup requires at least one verdict_decision_id")
        if len(normalized) != len(set(normalized)):
            raise DedupCodecError("Dedup contains duplicate verdict_decision_ids")
        for verdict_decision_id in normalized:
            if verdict_decision_id not in self.expected_ids:
                raise DedupCodecError(
                    f"Dedup references unknown verdict: {verdict_decision_id}"
                )
            if verdict_decision_id in covered_ids:
                raise DedupCodecError(
                    f"Verdict {verdict_decision_id} already has a dedup decision"
                )
        return normalized

    def canonical_bytes(self, decisions: Sequence[DedupDecision]) -> bytes:
        """Canonicalize validated Dedup decisions to deterministic JSON."""

        payload = {
            "schema_version": self.schema_version,
            "decisions": [self._to_dto(d) for d in decisions],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _validate_coverage(self, decisions: Sequence[DedupDecisionDto | DedupDecision]) -> None:
        covered_ids: set[str] = set()
        for decision in decisions:
            verdict_decision_id = decision.verdict_decision_id
            if verdict_decision_id not in self.expected_ids:
                raise DedupCodecError(
                    f"Dedup references unknown verdict: {verdict_decision_id}"
                )
            if verdict_decision_id in covered_ids:
                raise DedupCodecError(
                    f"Verdict {verdict_decision_id} is covered by multiple dedup decisions"
                )
            covered_ids.add(verdict_decision_id)
        if covered_ids != self.expected_ids:
            missing = sorted(self.expected_ids - covered_ids)
            raise DedupCodecError(
                f"Dedup does not cover all survived findings. Missing: {missing}"
            )

    @staticmethod
    def _to_domain(dto: DedupDecisionDto) -> DedupDecision:
        return DedupDecision(
            verdict_decision_id=dto.verdict_decision_id,
            outcome=DedupOutcome(dto.outcome),
            decision_source=DedupDecisionSource.LLM,
        )

    @staticmethod
    def _to_dto(decision: DedupDecision) -> dict[str, Any]:
        return {
            "verdict_decision_id": decision.verdict_decision_id,
            "outcome": decision.outcome.value,
        }

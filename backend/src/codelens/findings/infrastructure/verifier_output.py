import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from codelens.findings.domain.resolution import VerificationDecision

_Identity = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_-]*$",
    ),
]
_Reason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerificationDecisionDto(_StrictModel):
    cluster_id: _Identity
    outcome: Literal["confirmed", "rejected", "unresolved"]
    reason: _Reason


class VerifierSubmissionDto(_StrictModel):
    schema_version: Literal["1"]
    decisions: Annotated[list[VerificationDecisionDto], Field(max_length=64)]


class VerificationValidationError(ValueError):
    """Reject Verifier output that is incomplete or references an unknown Cluster."""


@dataclass(frozen=True)
class ValidatedVerificationBatch:
    decisions: tuple[VerificationDecision, ...]


class VerifierOutputCodec:
    """Accept only one outcome and reason for every supplied verification Cluster."""

    def __init__(self, cluster_ids: tuple[str, ...]) -> None:
        if len(cluster_ids) != len(set(cluster_ids)):
            raise ValueError("Verifier targets contain duplicate Clusters")
        self._cluster_ids = tuple(sorted(cluster_ids))

    def decode(self, payload: object) -> tuple[VerificationDecision, ...]:
        try:
            if isinstance(payload, VerifierSubmissionDto):
                submission = payload
            elif isinstance(payload, bytes | str):
                submission = VerifierSubmissionDto.model_validate_json(payload)
            elif isinstance(payload, Mapping):
                submission = VerifierSubmissionDto.model_validate(dict(payload))
            else:
                raise VerificationValidationError("Verifier output must be a JSON object")
        except ValidationError as error:
            raise VerificationValidationError("Verifier output schema is invalid") from error
        submitted_ids = tuple(item.cluster_id for item in submission.decisions)
        if len(submitted_ids) != len(set(submitted_ids)) or set(submitted_ids) != set(
            self._cluster_ids
        ):
            raise VerificationValidationError(
                "Verifier must decide exactly once for every supplied Cluster"
            )
        batch_ids = self._cluster_ids
        factories = {
            "confirmed": VerificationDecision.confirmed,
            "rejected": VerificationDecision.rejected,
            "unresolved": VerificationDecision.unresolved,
        }
        return tuple(
            factories[item.outcome](
                target_id=item.cluster_id,
                batch_target_ids=batch_ids,
                reason_code=item.reason,
            )
            for item in submission.decisions
        )

    def validate(
        self, decisions: Sequence[VerificationDecision]
    ) -> tuple[VerificationDecision, ...]:
        return self.decode(
            VerifierSubmissionDto(
                schema_version="1",
                decisions=[
                    VerificationDecisionDto(
                        cluster_id=item.target_id,
                        outcome=item.outcome.value,
                        reason=item.reason_code or "unspecified",
                    )
                    for item in decisions
                ],
            )
        )

    def canonical_bytes(self, decisions: Sequence[VerificationDecision]) -> bytes:
        validated = self.validate(decisions)
        return json.dumps(
            {
                "decisions": [
                    {
                        "cluster_id": item.target_id,
                        "outcome": item.outcome.value,
                        "reason": item.reason_code,
                    }
                    for item in validated
                ],
                "schema_version": "1",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

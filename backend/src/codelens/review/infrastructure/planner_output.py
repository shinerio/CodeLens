import json
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from codelens.review.application.planning import PlannerSelection


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannerSelectionDto(_StrictModel):
    schema_version: Literal["2"]
    reviewer_references: Annotated[list[str], Field(min_length=1, max_length=32)]


class PlannerOutputCodec:
    """Validate Planner selections against frozen host-owned inputs.

    Supports batch submission: each call to `decode_batch` validates a partial
    set of reviewer references, while `decode_final` validates that the
    accumulated set exactly matches the eligible reviewers.
    """

    def __init__(
        self,
        *,
        eligible_reviewer_references: tuple[str, ...],
        unavailable_reviewer_references: tuple[str, ...],
    ) -> None:
        if len(eligible_reviewer_references) != len(set(eligible_reviewer_references)):
            raise ValueError("eligible Reviewer references must be unique")
        self._eligible = frozenset(eligible_reviewer_references)
        self._unavailable = frozenset(unavailable_reviewer_references)
        if not self._unavailable.issubset(self._eligible):
            raise ValueError("unavailable Reviewers must belong to the eligible Catalog")

    def decode_batch(self, payload: object) -> tuple[str, ...]:
        """Validate one batch of reviewer references.

        Each reference must belong to the eligible set and must not be
        unavailable. Duplicates within the batch are rejected.
        """
        value = self._parse_dto(payload)
        references = tuple(value.reviewer_references)
        if len(references) != len(set(references)):
            raise ValueError("Planner reviewer references must be unique within a batch")
        unknown = set(references) - self._eligible
        if unknown:
            raise ValueError(f"Planner selected unknown Reviewers: {sorted(unknown)}")
        unavailable = set(references) & self._unavailable
        if unavailable:
            raise ValueError(f"Planner selected unavailable Reviewers: {sorted(unavailable)}")
        return references

    def decode_final(self, accumulated: frozenset[str]) -> PlannerSelection:
        """Validate that the accumulated set exactly matches eligible reviewers."""
        if accumulated != self._eligible:
            missing = self._eligible - accumulated
            extra = accumulated - self._eligible
            parts = []
            if missing:
                parts.append(f"missing: {sorted(missing)}")
            if extra:
                parts.append(f"extra: {sorted(extra)}")
            raise ValueError(f"Planner selection incomplete: {', '.join(parts)}")
        return PlannerSelection(
            schema_version="2",
            reviewer_references=tuple(sorted(accumulated)),
        )

    def decode(self, payload: object) -> PlannerSelection:
        """Validate a complete one-shot v2 selection."""
        value = self._parse_dto(payload)
        references = tuple(value.reviewer_references)
        if len(references) != len(set(references)):
            raise ValueError("Planner reviewer references must be unique")
        if set(references) != self._eligible:
            raise ValueError("Planner must decide exactly once for every eligible Reviewer")
        if any(reference in self._unavailable for reference in references):
            raise ValueError("Planner selected an unavailable Reviewer")
        return PlannerSelection(
            schema_version="2",
            reviewer_references=references,
        )

    def _parse_dto(self, payload: object) -> PlannerSelectionDto:
        if isinstance(payload, bytes):
            return PlannerSelectionDto.model_validate_json(payload)
        elif isinstance(payload, str):
            return PlannerSelectionDto.model_validate_json(payload)
        elif isinstance(payload, Mapping):
            return PlannerSelectionDto.model_validate(dict(payload))
        else:
            raise ValueError("Planner output must be a JSON object")

    def canonical_bytes(self, selection: PlannerSelection) -> bytes:
        payload = {
            "reviewer_references": list(selection.reviewer_references),
            "schema_version": selection.schema_version,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

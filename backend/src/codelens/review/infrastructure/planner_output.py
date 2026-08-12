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
    """Validate one focused Planner selection against frozen host-owned inputs."""

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

    def decode(self, payload: object) -> PlannerSelection:
        """Validate General alone or a team of at least two specialists."""

        value = self._parse_dto(payload)
        references = tuple(value.reviewer_references)
        if len(references) != len(set(references)):
            raise ValueError("Planner reviewer references must be unique")
        selected = set(references)
        unknown = selected - self._eligible
        if unknown:
            raise ValueError(f"Planner selected unknown Reviewers: {sorted(unknown)}")
        unavailable = selected & self._unavailable
        if unavailable:
            raise ValueError(f"Planner selected unavailable Reviewers: {sorted(unavailable)}")
        if "general:v2" in selected and selected != {"general:v2"}:
            raise ValueError("General reviewer must run alone")
        if "general:v2" not in selected and len(selected) < 2:
            raise ValueError("Planner specialist team requires at least two Reviewers")
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

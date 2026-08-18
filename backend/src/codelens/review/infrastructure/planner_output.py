import json
from typing import Literal

from codelens.review.application.planning import PlannerSelection


class PlannerOutputCodec:
    """Validate one focused Planner selection against frozen host-owned inputs.

    The Planner model submits only the flat ``reviewer_references`` list; the
    frozen ``schema_version`` is a host-owned constant injected server-side so
    the model never encodes (or double-encodes) a nested JSON envelope.
    """

    _SCHEMA_VERSION: Literal["2"] = "2"

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

    def decode_references(self, reviewer_references: list[str]) -> PlannerSelection:
        """Validate General alone or a team of at least two specialists."""

        references = tuple(reviewer_references)
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
        if "general:v2" not in selected and len(selected) < 1:
            raise ValueError("Planner specialist team requires at least one Reviewer")
        return PlannerSelection(
            schema_version=self._SCHEMA_VERSION,
            reviewer_references=references,
        )

    def canonical_bytes(self, selection: PlannerSelection) -> bytes:
        payload = {
            "reviewer_references": list(selection.reviewer_references),
            "schema_version": selection.schema_version,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

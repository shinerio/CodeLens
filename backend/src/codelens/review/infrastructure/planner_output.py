import json
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from codelens.review.application.planning import (
    PlannerReviewerDecision,
    PlannerRiskSignal,
    PlannerSelection,
)

BoundedCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    ),
]
BoundedPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannerRiskSignalDto(_StrictModel):
    code: BoundedCode
    evidence_paths: Annotated[list[BoundedPath], Field(max_length=64)]


class PlannerReviewerDecisionDto(_StrictModel):
    reviewer_reference: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=4,
            max_length=128,
            pattern=r"^[a-z][a-z0-9_.-]*:v[1-9][0-9]*$",
        ),
    ]
    is_selected: bool
    reason_codes: Annotated[list[BoundedCode], Field(min_length=1, max_length=8)]
    focus_paths: Annotated[list[BoundedPath], Field(max_length=64)]


class PlannerSelectionDto(_StrictModel):
    schema_version: Literal["1"]
    strategy: Literal["generalist", "specialist_team"]
    risk_signals: Annotated[list[PlannerRiskSignalDto], Field(max_length=64)]
    reviewer_decisions: Annotated[
        list[PlannerReviewerDecisionDto], Field(min_length=1, max_length=32)
    ]


class PlannerOutputCodec:
    """Decode one bounded Planner selection against frozen host-owned inputs."""

    def __init__(
        self,
        *,
        eligible_reviewer_references: tuple[str, ...],
        unavailable_reviewer_references: tuple[str, ...],
        target_paths: tuple[str, ...],
        allowed_reason_codes: frozenset[str],
    ) -> None:
        if len(eligible_reviewer_references) != len(set(eligible_reviewer_references)):
            raise ValueError("eligible Reviewer references must be unique")
        self._eligible = frozenset(eligible_reviewer_references)
        self._unavailable = frozenset(unavailable_reviewer_references)
        if not self._unavailable.issubset(self._eligible):
            raise ValueError("unavailable Reviewers must belong to the eligible Catalog")
        if any(
            path.startswith("/")
            or "\\" in path
            or "\0" in path
            or ".." in path.split("/")
            for path in target_paths
        ):
            raise ValueError("Planner target paths must be normalized Snapshot paths")
        self._target_paths = frozenset(target_paths)
        self._allowed_reasons = allowed_reason_codes

    def decode(self, payload: object) -> PlannerSelection:
        if isinstance(payload, bytes):
            value = PlannerSelectionDto.model_validate_json(payload)
        elif isinstance(payload, str):
            value = PlannerSelectionDto.model_validate_json(payload)
        elif isinstance(payload, Mapping):
            value = PlannerSelectionDto.model_validate(dict(payload))
        else:
            raise ValueError("Planner output must be a JSON object")
        references = tuple(item.reviewer_reference for item in value.reviewer_decisions)
        if len(references) != len(set(references)) or set(references) != self._eligible:
            raise ValueError("Planner must decide exactly once for every eligible Reviewer")
        selected = tuple(item for item in value.reviewer_decisions if item.is_selected)
        if not selected:
            raise ValueError("Planner must select a Reviewer strategy")
        if any(item.reviewer_reference in self._unavailable for item in selected):
            raise ValueError("Planner selected an unavailable Reviewer")
        for item in value.reviewer_decisions:
            self._validate_codes_and_paths(item.reason_codes, item.focus_paths)
        for signal in value.risk_signals:
            if signal.code not in self._allowed_reasons:
                raise ValueError("Planner used an unknown risk signal code")
            self._validate_paths(signal.evidence_paths)
        selected_references = tuple(item.reviewer_reference for item in selected)
        if value.strategy == "generalist":
            if selected_references != ("general:v1",):
                raise ValueError("generalist strategy must select only General")
        elif "general:v1" in selected_references:
            raise ValueError("specialist team cannot include General")
        return PlannerSelection(
            schema_version="1",
            strategy=value.strategy,
            risk_signals=tuple(
                PlannerRiskSignal(signal.code, tuple(signal.evidence_paths))
                for signal in value.risk_signals
            ),
            reviewer_decisions=tuple(
                PlannerReviewerDecision(
                    item.reviewer_reference,
                    item.is_selected,
                    tuple(item.reason_codes),
                    tuple(item.focus_paths),
                )
                for item in value.reviewer_decisions
            ),
        )

    def canonical_bytes(self, selection: PlannerSelection) -> bytes:
        payload = {
            "reviewer_decisions": [
                {
                    "focus_paths": list(item.focus_paths),
                    "is_selected": item.is_selected,
                    "reason_codes": list(item.reason_codes),
                    "reviewer_reference": item.reviewer_reference,
                }
                for item in selection.reviewer_decisions
            ],
            "risk_signals": [
                {"code": item.code, "evidence_paths": list(item.evidence_paths)}
                for item in selection.risk_signals
            ],
            "schema_version": selection.schema_version,
            "strategy": selection.strategy,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _validate_codes_and_paths(
        self, reason_codes: list[str], focus_paths: list[str]
    ) -> None:
        if len(reason_codes) != len(set(reason_codes)):
            raise ValueError("Planner reason codes must be unique")
        if not set(reason_codes).issubset(self._allowed_reasons):
            raise ValueError("Planner used an unknown reason code")
        self._validate_paths(focus_paths)

    def _validate_paths(self, paths: list[str]) -> None:
        if len(paths) != len(set(paths)):
            raise ValueError("Planner paths must be unique")
        for path in paths:
            if (
                path.startswith("/")
                or "\\" in path
                or "\0" in path
                or ".." in path.split("/")
                or path not in self._target_paths
            ):
                raise ValueError("Planner path is outside the frozen Snapshot")

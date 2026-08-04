from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, cast

from codelens.plugin.domain.models import ExportResult
from codelens.review.application.export_findings import FindingExportEnvelopeV2


@dataclass(frozen=True)
class FixedReviewerSelection:
    mode: Literal["fixed"]
    reviewer_versions: tuple[str, ...]


@dataclass(frozen=True)
class AdaptiveReviewerSelection:
    mode: Literal["adaptive"]


type ReviewerSelection = FixedReviewerSelection | AdaptiveReviewerSelection


class SupersedePolicy(StrEnum):
    LATEST_SNAPSHOT = "latest_snapshot"
    PRESERVE_ALL = "preserve_all"


@dataclass(frozen=True)
class TriggerReviewPolicy:
    reviewer_selection: ReviewerSelection
    supersede_policy: SupersedePolicy
    prompt_locale: Literal["en", "zh-CN"]

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "TriggerReviewPolicy":
        selection = _parse_reviewer_selection(config.get("reviewer_selection"))
        supersede = _required_string(config, "supersede_policy")
        locale = _required_string(config, "prompt_locale")
        if locale not in ("en", "zh-CN"):
            raise ValueError("unsupported prompt_locale")
        return cls(
            reviewer_selection=selection,
            supersede_policy=SupersedePolicy(supersede),
            prompt_locale=cast(Literal["en", "zh-CN"], locale),
        )


def _required_string(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _parse_reviewer_selection(value: object) -> ReviewerSelection:
    if not isinstance(value, Mapping):
        raise ValueError("reviewer_selection must be an object")
    mode = value.get("mode")
    if mode == "adaptive":
        if set(value) != {"mode"}:
            raise ValueError("adaptive selection accepts only mode")
        return AdaptiveReviewerSelection(mode="adaptive")
    if mode != "fixed":
        raise ValueError("unsupported reviewer selection mode")
    if set(value) != {"mode", "reviewer_versions"}:
        raise ValueError("fixed selection has unknown or missing fields")
    raw_references = value.get("reviewer_versions")
    if not isinstance(raw_references, list) or not all(
        isinstance(item, str) and item for item in raw_references
    ):
        raise ValueError("reviewer_versions must be a non-empty string list")
    references = tuple(raw_references)
    if not references or len(references) != len(set(references)):
        raise ValueError("reviewer_versions must be non-empty and unique")
    if "general:v1" in references and references != ("general:v1",):
        raise ValueError("general:v1 must be the only reviewer")
    if "correctness:v1" in references and references != ("correctness:v1",):
        raise ValueError("correctness:v1 is legacy single-reviewer only")
    return FixedReviewerSelection(mode="fixed", reviewer_versions=references)


class ReviewCreatorPort(Protocol):
    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        review_policy: TriggerReviewPolicy,
        external_context: dict[str, object] | None = None,
    ) -> str: ...


class ReportSinkPort(Protocol):
    """Deliver only the stable Published-Finding envelope to a report target."""

    async def export(
        self,
        envelope: FindingExportEnvelopeV2,
        config: Mapping[str, object],
        repository_path: Path,
    ) -> ExportResult: ...


__all__ = [
    "AdaptiveReviewerSelection",
    "FindingExportEnvelopeV2",
    "FixedReviewerSelection",
    "ReportSinkPort",
    "ReviewCreatorPort",
    "SupersedePolicy",
    "TriggerReviewPolicy",
]

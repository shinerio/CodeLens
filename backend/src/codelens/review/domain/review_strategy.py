import re
from dataclasses import dataclass, field
from typing import Literal

_REFERENCE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*:v2$")


def _validate_references(references: tuple[str, ...]) -> None:
    if not references:
        raise ValueError("Fixed reviewer selection requires at least one reviewer")
    if len(references) != len(set(references)):
        raise ValueError("Fixed reviewer selection contains duplicate reviewers")
    if any(_REFERENCE_PATTERN.fullmatch(reference) is None for reference in references):
        raise ValueError("Fixed reviewer selection contains an invalid reference")
    if "general:v2" in references and references != ("general:v2",):
        raise ValueError("General reviewer must run alone")


@dataclass(frozen=True)
class FixedReviewerSelection:
    """Freeze an explicit non-empty Reviewer set without allowing model selection."""

    reviewer_versions: tuple[str, ...]
    mode: Literal["fixed"] = field(default="fixed", init=False)

    def __post_init__(self) -> None:
        _validate_references(self.reviewer_versions)


@dataclass(frozen=True)
class AdaptiveReviewerSelection:
    """Request host-controlled planning without accepting user-selected Reviewers."""

    mode: Literal["adaptive"] = field(default="adaptive", init=False)


type ReviewerSelection = FixedReviewerSelection | AdaptiveReviewerSelection


@dataclass(frozen=True)
class ReviewProfileSnapshot:
    """Freeze one Review strategy and optional all-or-nothing template provenance."""

    reviewer_selection: ReviewerSelection
    source_profile_id: str | None = None
    source_profile_revision: int | None = None

    def __post_init__(self) -> None:
        if (self.source_profile_id is None) != (self.source_profile_revision is None):
            raise ValueError("source profile identity is incomplete")
        if self.source_profile_revision is not None and self.source_profile_revision < 1:
            raise ValueError("source profile revision must be positive")

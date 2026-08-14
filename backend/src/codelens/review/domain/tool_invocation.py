"""Bounded transcript metadata derived only from Tool Result v2 status."""

from dataclasses import dataclass
from typing import Literal

from codelens.review.domain.tool_results import classify_tool_result as classify_v2_result

type ToolInvocationStatus = Literal["accepted", "rejected", "unclassified"]


@dataclass(frozen=True)
class ToolInvocationOutcome:
    """Describe one tool attempt without retaining model-visible result content."""

    status: ToolInvocationStatus
    reason_code: str | None = None
    reason: str | None = None


def classify_tool_result(content: str) -> ToolInvocationOutcome:
    """Classify exclusively from a valid Tool Result v2 status."""

    classification = classify_v2_result(content)
    result = classification.result
    if result is None or not result.diagnostics:
        return ToolInvocationOutcome(classification.outcome)
    diagnostic = result.diagnostics[0]
    return ToolInvocationOutcome(
        classification.outcome,
        diagnostic.code,
        diagnostic.message,
    )


def outcome_metadata(outcome: ToolInvocationOutcome) -> dict[str, str]:
    """Serialize one classification into bounded transcript metadata."""

    metadata: dict[str, str] = {"tool_outcome": outcome.status}
    if outcome.reason_code is not None:
        metadata["tool_rejection_reason_code"] = outcome.reason_code[:128]
    if outcome.reason is not None:
        metadata["tool_rejection_reason"] = outcome.reason[:500]
    return metadata

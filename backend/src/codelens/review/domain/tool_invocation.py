"""Provider-neutral classification for model-visible tool invocation results."""

import json
from dataclasses import dataclass
from typing import Literal

type ToolInvocationStatus = Literal["accepted", "rejected"]


@dataclass(frozen=True)
class ToolInvocationOutcome:
    """Describe whether one tool attempt was accepted without retaining result content."""

    status: ToolInvocationStatus
    reason_code: str | None = None
    reason: str | None = None


def classify_tool_result(content: str) -> ToolInvocationOutcome:
    """Classify a sanitized tool result using bounded, provider-neutral signals."""

    value = _decode_nested_json(content)
    if isinstance(value, str):
        normalized = value.casefold()
        if "invalid json input for tool" in normalized or "validation error for" in normalized:
            return ToolInvocationOutcome(
                "rejected",
                "invalid_tool_arguments",
                "Tool arguments failed schema validation.",
            )
        if "an error occurred while running the tool" in normalized:
            return ToolInvocationOutcome(
                "rejected",
                "tool_invocation_error",
                "Tool execution failed before producing an accepted result.",
            )
        return ToolInvocationOutcome("accepted")
    if isinstance(value, dict):
        accepted = value.get("accepted")
        if accepted is False:
            reason_codes = _rejection_reason_codes(value)
            reason_code = reason_codes[0] if reason_codes else "tool_result_rejected"
            return ToolInvocationOutcome(
                "rejected",
                reason_code,
                f"Tool rejected the invocation ({reason_code}).",
            )
        if value.get("success") is False or value.get("ok") is False:
            return ToolInvocationOutcome(
                "rejected",
                "tool_result_error",
                "Tool completed with an unsuccessful result.",
            )
    return ToolInvocationOutcome("accepted")


def outcome_metadata(outcome: ToolInvocationOutcome) -> dict[str, str]:
    """Serialize a classification into bounded transcript metadata."""

    metadata: dict[str, str] = {"tool_outcome": outcome.status}
    if outcome.reason_code is not None:
        metadata["tool_rejection_reason_code"] = outcome.reason_code[:128]
    if outcome.reason is not None:
        metadata["tool_rejection_reason"] = outcome.reason[:500]
    return metadata


def _decode_nested_json(content: str) -> object:
    value: object = content
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break
    return value


def _rejection_reason_codes(value: dict[object, object]) -> tuple[str, ...]:
    direct = value.get("reason_code")
    if isinstance(direct, str) and direct:
        return (direct,)
    rejected = value.get("rejected_comments")
    if not isinstance(rejected, list):
        return ()
    codes: list[str] = []
    for item in rejected:
        if not isinstance(item, dict):
            continue
        code = item.get("reason_code")
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
    return tuple(codes)

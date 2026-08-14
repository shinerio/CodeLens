"""Provider-neutral values for every model-visible Tool Result v2 envelope."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type ToolHostOutcome = Literal["accepted", "rejected", "unclassified"]

_TOOL_RESULT_FIELDS = frozenset(
    {"schema_version", "tool", "status", "data", "diagnostics"}
)
_DIAGNOSTIC_FIELDS = frozenset(
    {"code", "message", "field", "retryable", "suggested_arguments"}
)


class ToolResultError(ValueError):
    """Reject an invalid or non-canonical model-visible tool result."""


class ToolResultStatus(StrEnum):
    """Classify one model-visible tool invocation without provider-specific signals."""

    SUCCESS = "success"
    PARTIAL = "partial"
    NEEDS_ACTION = "needs_action"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ToolDiagnostic:
    """Describe one stable, bounded diagnostic and an optional complete retry."""

    code: str
    message: str
    retryable: bool
    field: str | None = None
    suggested_arguments: dict[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.code.isascii():
            raise ToolResultError("diagnostic code must be non-empty ASCII")
        if not self.message:
            raise ToolResultError("diagnostic message must be non-empty")
        if not isinstance(self.retryable, bool):
            raise ToolResultError("diagnostic retryable must be a boolean")
        if self.field is not None and (not self.field or not self.field.isascii()):
            raise ToolResultError("diagnostic field must be non-empty ASCII")
        if self.suggested_arguments is not None:
            _ensure_json_object(self.suggested_arguments, "suggested_arguments")

    def as_payload(self) -> dict[str, JsonValue]:
        """Return the stable JSON representation without meaningless null fields."""

        payload: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.field is not None:
            payload["field"] = self.field
        if self.suggested_arguments is not None:
            payload["suggested_arguments"] = self.suggested_arguments
        return payload


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Represent the one legal model-visible Tool Result v2 envelope."""

    tool: str
    status: ToolResultStatus
    data: dict[str, JsonValue]
    diagnostics: tuple[ToolDiagnostic, ...] = ()
    schema_version: Literal["2"] = "2"

    def __post_init__(self) -> None:
        if self.schema_version != "2":
            raise ToolResultError("tool result schema version must be 2")
        if not self.tool or not self.tool.isascii():
            raise ToolResultError("tool name must be non-empty ASCII")
        if not isinstance(self.status, ToolResultStatus):
            raise ToolResultError("tool result status is invalid")
        _ensure_json_object(self.data, "data")
        if not all(isinstance(item, ToolDiagnostic) for item in self.diagnostics):
            raise ToolResultError("tool result diagnostics are invalid")

    def as_payload(self) -> dict[str, JsonValue]:
        """Return the exact stable top-level v2 payload."""

        return {
            "schema_version": self.schema_version,
            "tool": self.tool,
            "status": self.status.value,
            "data": self.data,
            "diagnostics": [diagnostic.as_payload() for diagnostic in self.diagnostics],
        }

    def to_json(self) -> str:
        """Serialize deterministically while preserving localized Unicode messages."""

        return json.dumps(
            self.as_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def with_diagnostic(self, diagnostic: ToolDiagnostic) -> ToolResult:
        """Return a new immutable result with one diagnostic appended."""

        return ToolResult(
            tool=self.tool,
            status=self.status,
            data=self.data,
            diagnostics=(*self.diagnostics, diagnostic),
        )


@dataclass(frozen=True, slots=True)
class ToolResultClassification:
    """Expose the sole host outcome derived from a parsed Tool Result status."""

    outcome: ToolHostOutcome
    result: ToolResult | None


def parse_tool_result(content: str) -> ToolResult:
    """Parse and strictly validate one non-nested Tool Result JSON object."""

    try:
        payload: object = json.loads(content)
    except json.JSONDecodeError as error:
        raise ToolResultError("tool result is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ToolResultError("tool result must be a JSON object")
    if set(payload) != _TOOL_RESULT_FIELDS:
        raise ToolResultError("tool result has invalid top-level fields")
    if payload.get("schema_version") != "2":
        raise ToolResultError("tool result schema version must be 2")
    tool = payload.get("tool")
    status_value = payload.get("status")
    data = payload.get("data")
    diagnostics_value = payload.get("diagnostics")
    if not isinstance(tool, str):
        raise ToolResultError("tool result tool must be a string")
    if not isinstance(status_value, str):
        raise ToolResultError("tool result status is invalid")
    try:
        status = ToolResultStatus(status_value)
    except (TypeError, ValueError) as error:
        raise ToolResultError("tool result status is invalid") from error
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise ToolResultError("tool result data must be an object")
    if not isinstance(diagnostics_value, list):
        raise ToolResultError("tool result diagnostics must be an array")
    diagnostics = tuple(_parse_diagnostic(value) for value in diagnostics_value)
    return ToolResult(tool, status, data, diagnostics)


def classify_tool_result(content: str) -> ToolResultClassification:
    """Classify only a valid v2 status; legacy and malformed results are unclassified."""

    try:
        result = parse_tool_result(content)
    except ToolResultError:
        return ToolResultClassification("unclassified", None)
    outcome: ToolHostOutcome = (
        "accepted"
        if result.status in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}
        else "rejected"
    )
    return ToolResultClassification(outcome, result)


def invalid_internal_tool_result(tool: str, message: str) -> ToolResult:
    """Build the mandatory safe fallback for an invalid adapter result."""

    return ToolResult(
        tool,
        ToolResultStatus.FAILED,
        {},
        (ToolDiagnostic("invalid_internal_tool_result", message, False),),
    )


def _parse_diagnostic(value: object) -> ToolDiagnostic:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ToolResultError("diagnostic must be an object")
    if not set(value).issubset(_DIAGNOSTIC_FIELDS) or not {
        "code",
        "message",
        "retryable",
    }.issubset(value):
        raise ToolResultError("diagnostic fields are invalid")
    code = value.get("code")
    message = value.get("message")
    retryable = value.get("retryable")
    field = value.get("field")
    suggested = value.get("suggested_arguments")
    if not isinstance(code, str) or not isinstance(message, str) or not isinstance(
        retryable, bool
    ):
        raise ToolResultError("diagnostic required fields are invalid")
    if field is not None and not isinstance(field, str):
        raise ToolResultError("diagnostic field is invalid")
    if suggested is not None and (
        not isinstance(suggested, dict)
        or not all(isinstance(key, str) for key in suggested)
    ):
        raise ToolResultError("diagnostic suggested arguments are invalid")
    return ToolDiagnostic(code, message, retryable, field, suggested)


def _ensure_json_object(value: dict[str, JsonValue], field_name: str) -> None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ToolResultError(f"tool result {field_name} must be an object")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ToolResultError(f"tool result {field_name} must be JSON serializable") from error

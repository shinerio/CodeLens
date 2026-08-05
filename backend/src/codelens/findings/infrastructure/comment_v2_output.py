import json
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from codelens.review.domain.tool_limits import ToolLimits

_LIMITS = ToolLimits()
_ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=_LIMITS.short_text_max),
]
_LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=_LIMITS.long_text_max),
]
_Path = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=_LIMITS.max_path_chars),
]


class CommentV2FindingSchema(BaseModel):
    """Validate only the bounded fields exposed by the Comment v2 model tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_id: _ShortText
    path: _Path
    side: Literal["old", "new"]
    existing_code: _LongText
    title: _ShortText
    content: _LongText
    recommendation: _LongText
    category: _ShortText
    severity: Literal["critical", "high", "medium", "low", "info"]
    primary_dimension: _ShortText
    evidence_strength: Literal["direct", "inferred", "weak"]


class CommentV2BatchSchema(BaseModel):
    """Version the strict model-facing Comment v2 submission envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["2"]
    findings: tuple[CommentV2FindingSchema, ...] = Field(max_length=100)


class CommentV2OutputCodecError(ValueError):
    """Reject malformed or incorrectly versioned Comment v2 output."""


@dataclass(frozen=True)
class CommentV2OutputCodec:
    """Canonicalize the Comment v2 transport envelope without accepting v1 fields."""

    schema_version: Literal["2"] = "2"

    def __post_init__(self) -> None:
        if self.schema_version != "2":
            raise CommentV2OutputCodecError("unsupported Comment v2 schema version")

    def encode(self, final_output: object) -> bytes:
        """Revalidate untrusted output and return deterministic UTF-8 JSON."""

        try:
            batch = CommentV2BatchSchema.model_validate(final_output)
        except ValidationError as error:
            raise CommentV2OutputCodecError(
                "Agent output does not match Comment v2"
            ) from error
        return json.dumps(
            batch.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def decode(self, payload: bytes) -> CommentV2BatchSchema:
        """Validate persisted bytes before returning model-facing values."""

        try:
            decoded = json.loads(payload)
            return CommentV2BatchSchema.model_validate(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise CommentV2OutputCodecError("persisted Comment v2 output is invalid") from error

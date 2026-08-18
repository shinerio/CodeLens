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


class CommentFindingSchema(BaseModel):
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


class CommentBatchSchema(BaseModel):
    """Version the strict model-facing Comment v2 submission envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["2"]
    findings: tuple[CommentFindingSchema, ...] = Field(max_length=100)


class CommentOutputCodecError(ValueError):
    """Reject malformed or incorrectly versioned Comment v2 output."""


@dataclass(frozen=True)
class CommentOutputCodec:
    """Canonicalize the Comment v2 transport envelope without accepting v1 fields."""

    schema_version: Literal["2"] = "2"

    def __post_init__(self) -> None:
        if self.schema_version != "2":
            raise CommentOutputCodecError("unsupported Comment schema version")

    def encode(self, final_output: object) -> bytes:
        """Revalidate untrusted output and return deterministic UTF-8 JSON."""

        try:
            batch = CommentBatchSchema.model_validate(final_output)
        except ValidationError as error:
            raise CommentOutputCodecError("Agent output does not match Comment v2") from error
        return json.dumps(
            batch.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def decode(self, payload: bytes) -> CommentBatchSchema:
        """Validate persisted bytes before returning model-facing values."""

        try:
            decoded = json.loads(payload)
            return CommentBatchSchema.model_validate(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise CommentOutputCodecError("persisted Comment output is invalid") from error

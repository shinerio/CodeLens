from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

_ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
_LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
_Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_Path = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


class SourceLocationSchema(BaseModel):
    """Validate one model-supplied Snapshot location before deterministic checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: _Path
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    side: Literal["old", "new"]
    excerpt_hash: _Hash
    is_deleted: bool = False

    @model_validator(mode="after")
    def validate_range_and_side(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.is_deleted and self.side != "old":
            raise ValueError("deleted locations must use the old side")
        return self


class EvidenceSchema(BaseModel):
    """Validate bounded evidence metadata without embedding unbounded tool output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["excerpt", "tool", "data_flow", "test"]
    description: _LongText
    artifact_ref: _ShortText | None = None
    excerpt_hash: _Hash | None = None


class RuleReferenceSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: _Path
    content_hash: _Hash


class FindingCandidateSchema(BaseModel):
    """Validate untrusted model output before IDs or fingerprints are derived."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_id: _ShortText
    category: _ShortText
    title: _ShortText
    severity: Literal["critical", "high", "medium", "low", "info"]
    disposition: Literal["blocking", "non_blocking", "pre_existing"]
    confidence: float = Field(ge=0.0, le=1.0)
    primary_location: SourceLocationSchema
    related_locations: tuple[SourceLocationSchema, ...] = Field(default=(), max_length=20)
    changed_hunk_id: _ShortText | None = None
    change_origin: Literal["introduced", "exposed", "pre_existing", "unknown"]
    evidence: tuple[EvidenceSchema, ...] = Field(min_length=1, max_length=20)
    impact: _LongText
    explanation: _LongText
    reproduction: _LongText | None = None
    recommendation: _LongText
    suggested_patch: Annotated[str, StringConstraints(max_length=20_000)] | None = None
    rule_sources: tuple[RuleReferenceSchema, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def require_change_evidence(self) -> Self:
        if self.changed_hunk_id is None and not any(
            item.kind == "data_flow" for item in self.evidence
        ):
            message = "Finding requires a changed hunk or explicit change-propagation evidence"
            raise ValueError(message)
        return self


class FindingBatchSchema(BaseModel):
    """Version the complete bounded model output envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"]
    findings: tuple[FindingCandidateSchema, ...] = Field(max_length=100)

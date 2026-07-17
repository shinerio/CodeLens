from dataclasses import dataclass
from enum import StrEnum


class FindingSeverity(StrEnum):
    """Stable report severity values ordered by review policy."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingDisposition(StrEnum):
    """Classify whether a Finding blocks the reviewed change."""

    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"
    PRE_EXISTING = "pre_existing"


class ChangeOrigin(StrEnum):
    """Describe how a Finding relates to the target change."""

    INTRODUCED = "introduced"
    EXPOSED = "exposed"
    PRE_EXISTING = "pre_existing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceLocation:
    """Locate validated evidence on one immutable Snapshot side."""

    path: str
    start_line: int
    end_line: int
    side: str
    excerpt_hash: str
    is_deleted: bool


@dataclass(frozen=True)
class Evidence:
    """Reference bounded evidence supporting a trusted Finding."""

    kind: str
    description: str
    artifact_ref: str | None
    excerpt_hash: str | None


@dataclass(frozen=True)
class RuleReference:
    """Reference the immutable instruction source applied to a Finding."""

    path: str
    content_hash: str


@dataclass(frozen=True)
class Finding:
    """Contain only application-derived identity and fully validated evidence."""

    finding_id: str
    fingerprint: str
    reviewer_id: str
    category: str
    title: str
    severity: FindingSeverity
    disposition: FindingDisposition
    confidence: float
    primary_location: SourceLocation
    related_locations: tuple[SourceLocation, ...]
    changed_hunk_id: str | None
    change_origin: ChangeOrigin
    evidence: tuple[Evidence, ...]
    impact: str
    explanation: str
    reproduction: str | None
    recommendation: str
    suggested_patch: str | None
    rule_sources: tuple[RuleReference, ...]


@dataclass(frozen=True)
class FindingBatch:
    """Group trusted Findings under one stable output schema version."""

    schema_version: str
    findings: tuple[Finding, ...]

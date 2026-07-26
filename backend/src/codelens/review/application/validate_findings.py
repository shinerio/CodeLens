"""Deterministic validation of untrusted reviewer Findings."""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Protocol, cast

from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingBatch,
    FindingDisposition,
    FindingSeverity,
    RuleReference,
    SourceLocation,
)
from codelens.review.domain.ports import (
    AgentOutputCodecPort,
    FindingValidationWarning,
    SnapshotFileReaderPort,
)
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.workspace.domain.models import ReviewSnapshot


class FindingValidationError(ValueError):
    """Reject output that cannot be tied to immutable Snapshot evidence."""


class _LocationCandidate(Protocol):
    path: str
    start_line: int
    end_line: int
    side: str
    excerpt_hash: str
    is_deleted: bool


class _EvidenceCandidate(Protocol):
    kind: str
    description: str
    artifact_ref: str | None
    excerpt_hash: str | None


class _RuleCandidate(Protocol):
    path: str
    content_hash: str


class _FindingCandidate(Protocol):
    reviewer_id: str
    category: str
    title: str
    severity: str
    disposition: str
    confidence: float
    primary_location: _LocationCandidate
    related_locations: tuple[_LocationCandidate, ...]
    changed_hunk_id: str | None
    change_origin: str
    evidence: tuple[_EvidenceCandidate, ...]
    impact: str
    explanation: str
    reproduction: str | None
    recommendation: str
    rule_sources: tuple[_RuleCandidate, ...]

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class _BatchCandidate(Protocol):
    schema_version: str
    findings: tuple[_FindingCandidate, ...]


class _DecoderPort(AgentOutputCodecPort, Protocol):
    def decode(self, payload: bytes) -> object: ...


class FindingValidator:
    """Validate and derive trusted Findings from untrusted checkpoint bytes."""

    def __init__(
        self,
        *,
        task_id: str,
        node_key: str,
        snapshot: ReviewSnapshot,
        agent: AgentVersion,
        codec: _DecoderPort,
        excerpt_reader: SnapshotFileReaderPort | None = None,
    ) -> None:
        self._task_id = task_id
        self._node_key = node_key
        self._snapshot = snapshot
        self._agent = agent
        self._codec = codec
        self._excerpt_reader = excerpt_reader
        self._warnings: tuple[FindingValidationWarning, ...] = ()

    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]:
        """Return bounded diagnostics for candidates skipped by the latest validation."""

        return self._warnings

    async def validate(self, payload: bytes) -> FindingBatch:
        """Validate candidates and keep the first occurrence of each trusted Finding."""

        self._warnings = ()
        try:
            decoded = cast(_BatchCandidate, self._codec.decode(payload))
        except (TypeError, ValueError, AttributeError) as error:
            raise FindingValidationError("Agent output schema is invalid") from error

        findings: list[Finding] = []
        warnings: list[FindingValidationWarning] = []
        seen_fingerprints: set[str] = set()
        for candidate_index, candidate in enumerate(decoded.findings):
            try:
                finding = await self._validate_candidate(candidate)
            except (FindingValidationError, ValueError) as error:
                warnings.append(
                    FindingValidationWarning(candidate_index, "invalid", str(error))
                )
                continue
            if finding.fingerprint in seen_fingerprints:
                warnings.append(
                    FindingValidationWarning(
                        candidate_index,
                        "duplicate",
                        "Finding duplicates an earlier validated candidate",
                    )
                )
                continue
            seen_fingerprints.add(finding.fingerprint)
            findings.append(finding)
        self._warnings = tuple(warnings)
        return FindingBatch(schema_version=decoded.schema_version, findings=tuple(findings))

    async def _validate_candidate(self, candidate: _FindingCandidate) -> Finding:
        if candidate.reviewer_id != self._agent.agent_id:
            raise FindingValidationError("Finding reviewer does not match the Agent")
        if candidate.confidence < self._agent.confidence_floor:
            raise FindingValidationError("Finding confidence is below the Agent threshold")
        primary = await self._location(candidate.primary_location)
        related = tuple([await self._location(item) for item in candidate.related_locations])
        hunk = None
        if candidate.changed_hunk_id is not None:
            hunk = next(
                (
                    item
                    for item in self._snapshot.change_index.hunks
                    if item.hunk_id == candidate.changed_hunk_id
                ),
                None,
            )
            if hunk is None or not (
                hunk.path == primary.path
                and hunk.side == primary.side
                and primary.start_line >= hunk.start_line
                and primary.end_line <= hunk.end_line
            ):
                raise FindingValidationError("Finding references an unknown changed hunk")

        if self._excerpt_reader is not None:
            excerpt = await self._excerpt_reader.read(
                self._snapshot,
                primary.path,
                primary.start_line,
                primary.end_line,
                primary.side,
                64 * 1024,
            )
            if excerpt.truncated:
                raise FindingValidationError("Finding location is not tied to a frozen excerpt")
            primary = SourceLocation(
                path=primary.path,
                start_line=primary.start_line,
                end_line=primary.end_line,
                side=primary.side,
                excerpt_hash=excerpt.content_hash,
                is_deleted=primary.is_deleted,
            )
        elif hunk is not None and hunk.excerpt_hash != primary.excerpt_hash:
            raise FindingValidationError("Finding location does not match its changed hunk")

        known_hashes = {location.excerpt_hash for location in (primary, *related)} | {
            item.excerpt_hash for item in self._snapshot.change_index.hunks
        }
        evidence = tuple(
            Evidence(item.kind, item.description, item.artifact_ref, item.excerpt_hash)
            for item in candidate.evidence
            if item.excerpt_hash is None or item.excerpt_hash in known_hashes
        )

        entries = {item.path: item for item in self._snapshot.manifest.entries}
        rules: list[RuleReference] = []
        for item in candidate.rule_sources:
            entry = entries.get(item.path)
            if (
                entry is None
                or entry.origin != "instruction"
                or entry.content_hash != item.content_hash
            ):
                continue
            rules.append(RuleReference(item.path, item.content_hash))

        canonical = json.dumps(
            candidate.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identity = hashlib.sha256(
            f"{self._task_id}\0{self._node_key}\0{fingerprint}".encode()
        ).hexdigest()
        return Finding(
            finding_id=f"finding_{identity}",
            fingerprint=fingerprint,
            reviewer_id=candidate.reviewer_id,
            category=candidate.category,
            title=candidate.title,
            severity=FindingSeverity(candidate.severity),
            disposition=FindingDisposition(candidate.disposition),
            confidence=candidate.confidence,
            primary_location=primary,
            related_locations=related,
            changed_hunk_id=candidate.changed_hunk_id,
            change_origin=ChangeOrigin(candidate.change_origin),
            evidence=evidence,
            impact=candidate.impact,
            explanation=candidate.explanation,
            reproduction=candidate.reproduction,
            recommendation=candidate.recommendation,
            rule_sources=tuple(rules),
        )

    async def _location(self, candidate: _LocationCandidate) -> SourceLocation:
        if not self._is_normalized_relative(candidate.path):
            raise FindingValidationError("Finding path is unsafe")
        entry = next(
            (item for item in self._snapshot.manifest.entries if item.path == candidate.path),
            None,
        )
        if entry is None or candidate.path not in {
            *self._snapshot.manifest.target_paths,
            *self._snapshot.manifest.context_paths,
        }:
            raise FindingValidationError("Finding path is outside the frozen Snapshot")
        if candidate.is_deleted != (entry.kind == "deleted"):
            raise FindingValidationError("Finding deletion metadata is stale")
        return SourceLocation(
            path=candidate.path,
            start_line=candidate.start_line,
            end_line=candidate.end_line,
            side=candidate.side,
            excerpt_hash=candidate.excerpt_hash,
            is_deleted=candidate.is_deleted,
        )

    @staticmethod
    def _is_normalized_relative(path: str) -> bool:
        candidate = PurePosixPath(path)
        return bool(
            path
            and "\0" not in path
            and "\\" not in path
            and not candidate.is_absolute()
            and ".." not in candidate.parts
            and candidate.as_posix() == path
        )

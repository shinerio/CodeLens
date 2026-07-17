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
from codelens.review.domain.ports import AgentOutputCodecPort
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
    suggested_patch: str | None
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
    ) -> None:
        self._task_id = task_id
        self._node_key = node_key
        self._snapshot = snapshot
        self._agent = agent
        self._codec = codec

    async def validate(self, payload: bytes) -> FindingBatch:
        """Apply schema, path, hunk, evidence, and identity checks in stable order."""

        try:
            decoded = cast(_BatchCandidate, self._codec.decode(payload))
            findings = tuple(self._validate_candidate(item) for item in decoded.findings)
        except FindingValidationError:
            raise
        except (TypeError, ValueError, AttributeError) as error:
            raise FindingValidationError("Agent output schema is invalid") from error
        fingerprints = [finding.fingerprint for finding in findings]
        if len(fingerprints) != len(set(fingerprints)):
            raise FindingValidationError("Agent output contains duplicate Findings")
        return FindingBatch(schema_version=decoded.schema_version, findings=findings)

    def _validate_candidate(self, candidate: _FindingCandidate) -> Finding:
        if candidate.reviewer_id != self._agent.agent_id:
            raise FindingValidationError("Finding reviewer does not match the Agent")
        if candidate.confidence < self._agent.confidence_floor:
            raise FindingValidationError("Finding confidence is below the Agent threshold")
        primary = self._location(candidate.primary_location)
        related = tuple(self._location(item) for item in candidate.related_locations)
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
            if hunk is None:
                raise FindingValidationError("Finding references an unknown changed hunk")
            if not (
                hunk.path == primary.path
                and hunk.side == primary.side
                and primary.start_line >= hunk.start_line
                and primary.end_line <= hunk.end_line
                and hunk.excerpt_hash == primary.excerpt_hash
            ):
                raise FindingValidationError("Finding location does not match its changed hunk")

        known_hashes = {
            location.excerpt_hash for location in (primary, *related)
        } | {item.excerpt_hash for item in self._snapshot.change_index.hunks}
        evidence = tuple(
            Evidence(item.kind, item.description, item.artifact_ref, item.excerpt_hash)
            for item in candidate.evidence
        )
        if any(
            item.excerpt_hash is not None and item.excerpt_hash not in known_hashes
            for item in evidence
        ):
            raise FindingValidationError("Finding evidence is not tied to a validated excerpt")

        entries = {item.path: item for item in self._snapshot.manifest.entries}
        rules: list[RuleReference] = []
        for item in candidate.rule_sources:
            entry = entries.get(item.path)
            if (
                entry is None
                or entry.origin != "instruction"
                or entry.content_hash != item.content_hash
            ):
                raise FindingValidationError("Finding rule source is not in the frozen Snapshot")
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
            suggested_patch=candidate.suggested_patch,
            rule_sources=tuple(rules),
        )

    def _location(self, candidate: _LocationCandidate) -> SourceLocation:
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

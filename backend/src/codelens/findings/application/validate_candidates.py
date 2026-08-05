import json
import re
from dataclasses import asdict
from pathlib import PurePosixPath

from codelens.findings.domain.candidates import (
    CandidateFinding,
    CandidateFindingBatch,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.review.domain.ports import FindingValidationWarning, SnapshotFileReaderPort
from codelens.reviewer_catalog.domain.models import AgentRole, AgentVersion
from codelens.workspace.domain.models import ReviewSnapshot

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ID = re.compile(r"^candidate_[0-9a-f]{64}$")


class CandidateValidationError(ValueError):
    """Reject Candidate state that is not tied to one frozen Agent Run and Snapshot."""


class CandidateBatchCodec:
    """Canonicalize already host-resolved Comment v2 Candidate domain values."""

    schema_version = "2"

    def encode(self, batch: CandidateFindingBatch) -> bytes:
        return json.dumps(
            asdict(batch),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def decode(self, payload: bytes) -> CandidateFindingBatch:
        try:
            value = json.loads(payload)
            if not isinstance(value, dict) or set(value) != {
                "candidates",
                "schema_version",
            }:
                raise ValueError("Candidate batch shape is invalid")
            if value["schema_version"] != "2" or not isinstance(
                value["candidates"], list
            ):
                raise ValueError("Candidate batch version is invalid")
            return CandidateFindingBatch(
                tuple(self._candidate(item) for item in value["candidates"])
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise CandidateValidationError("persisted Candidate output is invalid") from error

    @staticmethod
    def _candidate(value: object) -> CandidateFinding:
        if not isinstance(value, dict):
            raise CandidateValidationError("Candidate value must be an object")
        item = dict(value)
        primary = SourceLocation(**item.pop("primary_location"))
        related = tuple(SourceLocation(**location) for location in item.pop("related_locations"))
        severity = FindingSeverity(item.pop("severity"))
        evidence_strength = EvidenceStrength(item.pop("evidence_strength"))
        impact_certainty = ImpactCertainty(item.pop("impact_certainty"))
        reproducibility = Reproducibility(item.pop("reproducibility"))
        secondary_dimensions = tuple(item.pop("secondary_dimensions"))
        evidence_hashes = tuple(item.pop("evidence_hashes"))
        return CandidateFinding(
            **item,
            severity=severity,
            evidence_strength=evidence_strength,
            impact_certainty=impact_certainty,
            reproducibility=reproducibility,
            primary_location=primary,
            related_locations=related,
            secondary_dimensions=secondary_dimensions,
            evidence_hashes=evidence_hashes,
        )


class CandidateValidator:
    """Revalidate host-resolved Candidate output against frozen execution identity."""

    def __init__(
        self,
        *,
        task_id: str,
        run_id: str,
        snapshot: ReviewSnapshot,
        agent: AgentVersion,
        excerpt_reader: SnapshotFileReaderPort | None = None,
        codec: CandidateBatchCodec | None = None,
    ) -> None:
        if agent.role is not AgentRole.REVIEWER or agent.output_contract_version != "2":
            raise CandidateValidationError("Candidate validation requires a v2 Reviewer")
        self._task_id = task_id
        self._run_id = run_id
        self._snapshot = snapshot
        self._agent = agent
        self._excerpt_reader = excerpt_reader
        self._codec = codec or CandidateBatchCodec()
        self._warnings: tuple[FindingValidationWarning, ...] = ()

    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]:
        """Return bounded diagnostics for candidates skipped by the latest validation."""

        return self._warnings

    async def validate(
        self, batch: bytes | CandidateFindingBatch
    ) -> CandidateFindingBatch:
        """Best-effort validation: skip invalid candidates, keep valid ones.

        Schema-level errors (bad batch shape/version) still abort the entire
        batch. Individual candidate errors (bad identity, location, evidence)
        are recorded as warnings and the candidate is skipped.
        """

        decoded = self._codec.decode(batch) if isinstance(batch, bytes) else batch
        if decoded.schema_version != "2":
            raise CandidateValidationError("Candidate schema version is invalid")
        valid: list[CandidateFinding] = []
        warnings: list[FindingValidationWarning] = []
        seen_fingerprints: set[str] = set()
        for index, candidate in enumerate(decoded.candidates):
            try:
                await self._validate_candidate(candidate)
            except (CandidateValidationError, ValueError) as error:
                warnings.append(
                    FindingValidationWarning(index, "invalid", str(error))
                )
                continue
            if candidate.fingerprint in seen_fingerprints:
                warnings.append(
                    FindingValidationWarning(
                        index,
                        "duplicate",
                        "Candidate duplicates an earlier validated candidate",
                    )
                )
                continue
            seen_fingerprints.add(candidate.fingerprint)
            valid.append(candidate)
        self._warnings = tuple(warnings)
        return CandidateFindingBatch(tuple(valid))

    async def _validate_candidate(self, candidate: CandidateFinding) -> None:
        if (
            candidate.task_id != self._task_id
            or candidate.run_id != self._run_id
            or candidate.snapshot_id != self._snapshot.snapshot_id
        ):
            raise CandidateValidationError("Candidate execution identity is invalid")
        if candidate.reviewer_reference != self._agent.reference:
            raise CandidateValidationError("Candidate reviewer identity is invalid")
        if _CANDIDATE_ID.fullmatch(candidate.candidate_id) is None:
            raise CandidateValidationError("Candidate identifier is invalid")
        if _SHA256.fullmatch(candidate.fingerprint) is None:
            raise CandidateValidationError("Candidate fingerprint is invalid")
        if candidate.primary_dimension not in self._agent.dimensions:
            raise CandidateValidationError("Candidate primary dimension is invalid")
        if (
            len(candidate.secondary_dimensions) != len(set(candidate.secondary_dimensions))
            or candidate.primary_dimension in candidate.secondary_dimensions
        ):
            raise CandidateValidationError("Candidate secondary dimensions are invalid")
        await self._validate_location(candidate.primary_location, candidate.changed_hunk_id)
        for location in candidate.related_locations:
            await self._validate_location(location, None)
        if (
            _SHA256.fullmatch(candidate.existing_code_hash) is None
            or not candidate.evidence_hashes
            or candidate.existing_code_hash not in candidate.evidence_hashes
            or any(_SHA256.fullmatch(value) is None for value in candidate.evidence_hashes)
        ):
            raise CandidateValidationError("Candidate evidence identity is invalid")

    async def _validate_location(
        self, location: SourceLocation, changed_hunk_id: str | None
    ) -> None:
        path = PurePosixPath(location.path)
        if (
            not location.path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in location.path
            or location.path not in self._snapshot.manifest.target_paths
            or location.side not in {"old", "new"}
            or location.start_line < 1
            or location.end_line < location.start_line
        ):
            raise CandidateValidationError("Candidate location is invalid")
        entry = next(
            (item for item in self._snapshot.manifest.entries if item.path == location.path),
            None,
        )
        if entry is None or location.is_deleted != (entry.kind == "deleted"):
            raise CandidateValidationError("Candidate location deletion state is invalid")
        if changed_hunk_id is not None:
            hunk = next(
                (
                    item
                    for item in self._snapshot.change_index.hunks
                    if item.hunk_id == changed_hunk_id
                ),
                None,
            )
            if hunk is None or not (
                hunk.path == location.path
                and hunk.side == location.side
                and location.start_line >= hunk.start_line
                and location.end_line <= hunk.end_line
            ):
                raise CandidateValidationError("Candidate changed hunk is invalid")
        if self._excerpt_reader is not None:
            excerpt = await self._excerpt_reader.read(
                self._snapshot,
                location.path,
                location.start_line,
                location.end_line,
                location.side,
                64 * 1024,
            )
            if excerpt.truncated or excerpt.content_hash != location.excerpt_hash:
                raise CandidateValidationError("Candidate location evidence is invalid")
        elif changed_hunk_id is not None and hunk is not None:
            if hunk.excerpt_hash != location.excerpt_hash:
                raise CandidateValidationError("Candidate location evidence is invalid")

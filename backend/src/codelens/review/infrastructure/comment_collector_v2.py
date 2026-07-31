"""Resolve Comment v2 submissions against one immutable Review Snapshot."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

from codelens.findings.domain.candidates import (
    CandidateFinding,
    CandidateFindingBatch,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.models import FindingSeverity, SourceLocation
from codelens.findings.infrastructure.comment_v2_output import CommentV2FindingSchema
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.comment_collector import CommentCandidateRejectedError
from codelens.review.infrastructure.line_resolver import (
    resolve_from_file_content,
    resolve_from_hunk,
    split_and_normalize,
)
from codelens.workspace.domain.models import ReviewSnapshot


class _EvidenceTools(Protocol):
    """Expose only bounded Snapshot operations needed for comment resolution."""

    @property
    def review_file_paths(self) -> tuple[str, ...]: ...

    async def read_diff_for_resolution(self, path: str) -> str: ...

    async def read_full_file(
        self,
        path: str,
        version: Literal["base", "current"],
    ) -> str: ...

    async def excerpt_identity(
        self,
        path: str,
        start_line: int,
        end_line: int,
        version: Literal["base", "current"],
    ) -> tuple[str, bool]: ...


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_excerpt_hash(existing_code: str) -> str:
    normalized = "\n".join(split_and_normalize(existing_code)).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


@dataclass
class ReviewCommentCollectorV2:
    """Resolve Comment v2 items independently into trusted CandidateFinding values.

    The collector has no persistence, network, workspace, or process access. It
    accepts only the assigned reviewer's primary dimensions and derives every
    location, hunk, hash, and identity from the frozen Snapshot.
    """

    task_id: str
    run_id: str
    snapshot: ReviewSnapshot
    reviewer_reference: str
    reviewer_dimensions: tuple[str, ...]
    tools: _EvidenceTools
    tool_limits: ToolLimits = field(default_factory=ToolLimits)
    _candidates: list[CandidateFinding] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.-]*:v[1-9][0-9]*", self.reviewer_reference) is None:
            raise ValueError("Comment v2 reviewer reference is invalid")
        if not self.reviewer_dimensions:
            raise ValueError("Comment v2 reviewer requires at least one assigned dimension")
        if len(self.reviewer_dimensions) != len(set(self.reviewer_dimensions)):
            raise ValueError("Comment v2 reviewer dimensions contain duplicates")

    async def submit(self, submission: CommentV2FindingSchema) -> str:
        """Resolve one submission or reject it without retaining partial state."""

        expected_reviewer_id = self.reviewer_reference.rpartition(":v")[0]
        if submission.reviewer_id != expected_reviewer_id:
            raise CommentCandidateRejectedError(
                "comment reviewer does not match this Agent Run"
            )
        if submission.primary_dimension not in self.reviewer_dimensions:
            raise CommentCandidateRejectedError(
                "comment primary dimension is outside this reviewer's assignment"
            )
        if submission.path not in self.tools.review_file_paths:
            raise CommentCandidateRejectedError("comment path is outside this Review")

        start_line, end_line = await self._resolve_line_numbers(
            submission.path,
            submission.existing_code,
            submission.side,
        )
        hunks = tuple(
            hunk
            for hunk in self.snapshot.change_index.hunks
            if hunk.path == submission.path
            and hunk.side == submission.side
            and start_line >= hunk.start_line
            and end_line <= hunk.end_line
        )
        if len(hunks) != 1:
            raise CommentCandidateRejectedError(
                f"existing_code must quote only consecutive changed {submission.side}-side "
                "lines without diff markers; do not include unchanged context lines"
            )
        excerpt_hash, is_truncated = await self.tools.excerpt_identity(
            submission.path,
            start_line,
            end_line,
            "base" if submission.side == "old" else "current",
        )
        if is_truncated:
            raise CommentCandidateRejectedError(
                "comment location cannot be resolved to a complete frozen excerpt"
            )

        location = SourceLocation(
            path=submission.path,
            start_line=start_line,
            end_line=end_line,
            side=submission.side,
            excerpt_hash=excerpt_hash,
            is_deleted=self._is_deleted_path(submission.path),
        )
        existing_code_hash = _normalized_excerpt_hash(submission.existing_code)
        evidence_hashes = (existing_code_hash,)
        axes = {
            "evidence_strength": submission.evidence_strength,
            "impact_certainty": submission.impact_certainty,
            "reproducibility": submission.reproducibility,
        }
        fingerprint = _canonical_hash(
            {
                "snapshot_id": self.snapshot.snapshot_id,
                "location": {
                    "path": location.path,
                    "start_line": location.start_line,
                    "end_line": location.end_line,
                    "side": location.side,
                    "excerpt_hash": location.excerpt_hash,
                },
                "category": submission.category,
                "title": submission.title,
                "content": submission.content,
                "primary_dimension": submission.primary_dimension,
                "secondary_dimensions": submission.secondary_dimensions,
                "axes": axes,
                "evidence_hashes": evidence_hashes,
            }
        )
        candidate_identity = _canonical_hash(
            {
                "task_id": self.task_id,
                "run_id": self.run_id,
                "reviewer_reference": self.reviewer_reference,
                "location": {
                    "path": location.path,
                    "start_line": location.start_line,
                    "end_line": location.end_line,
                    "side": location.side,
                },
                "title": submission.title,
                "axes": axes,
            }
        )
        candidate_id = f"candidate_{candidate_identity}"
        if any(candidate.candidate_id == candidate_id for candidate in self._candidates):
            raise CommentCandidateRejectedError("comment duplicates an accepted candidate")
        self._candidates.append(
            CandidateFinding(
                task_id=self.task_id,
                candidate_id=candidate_id,
                run_id=self.run_id,
                snapshot_id=self.snapshot.snapshot_id,
                reviewer_reference=self.reviewer_reference,
                category=submission.category,
                title=submission.title,
                severity=FindingSeverity(submission.severity),
                primary_dimension=submission.primary_dimension,
                secondary_dimensions=submission.secondary_dimensions,
                evidence_strength=EvidenceStrength(submission.evidence_strength),
                impact_certainty=ImpactCertainty(submission.impact_certainty),
                reproducibility=Reproducibility(submission.reproducibility),
                primary_location=location,
                related_locations=(),
                changed_hunk_id=hunks[0].hunk_id,
                existing_code_hash=existing_code_hash,
                evidence_hashes=evidence_hashes,
                content=submission.content,
                recommendation=submission.recommendation,
                fingerprint=fingerprint,
            )
        )
        return json.dumps(
            {"accepted": True, "comment_count": len(self._candidates)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def submit_many(self, submissions: list[CommentV2FindingSchema]) -> str:
        """Retain valid submissions while reporting each rejected item by index."""

        if not submissions or len(submissions) > self.tool_limits.comment_batch_size:
            raise ValueError(
                f"comment requires between one and {self.tool_limits.comment_batch_size} comments"
            )
        accepted_count = 0
        rejected_comments: list[dict[str, object]] = []
        for index, submission in enumerate(submissions):
            try:
                await self.submit(submission)
            except CommentCandidateRejectedError as error:
                rejected_comments.append({"index": index, "reason": str(error)})
            else:
                accepted_count += 1
        return json.dumps(
            {
                "accepted": accepted_count > 0,
                "accepted_count": accepted_count,
                "comment_count": len(self._candidates),
                "rejected_comments": rejected_comments,
                "rejected_count": len(rejected_comments),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def candidate_batch(self) -> CandidateFindingBatch:
        """Return only fully resolved candidates in stable acceptance order."""

        return CandidateFindingBatch(tuple(self._candidates))

    async def _resolve_line_numbers(
        self,
        path: str,
        existing_code: str,
        side: Literal["old", "new"],
    ) -> tuple[int, int]:
        diff_result = json.loads(await self.tools.read_diff_for_resolution(path))
        diff_text = diff_result.get("content", "")
        if not isinstance(diff_text, str):
            raise CommentCandidateRejectedError("Snapshot diff response is invalid")
        resolved = resolve_from_hunk(diff_text, existing_code, side=side)
        if resolved is not None:
            return resolved
        file_content = await self.tools.read_full_file(
            path,
            "base" if side == "old" else "current",
        )
        resolved = resolve_from_file_content(file_content, existing_code)
        if resolved is not None:
            return resolved
        raise CommentCandidateRejectedError(
            "existing_code cannot be resolved to a line range"
        )

    def _is_deleted_path(self, path: str) -> bool:
        entry = next(
            (item for item in self.snapshot.manifest.entries if item.path == path),
            None,
        )
        if entry is None:
            raise CommentCandidateRejectedError(
                "comment path is outside the frozen Snapshot"
            )
        return entry.kind == "deleted"

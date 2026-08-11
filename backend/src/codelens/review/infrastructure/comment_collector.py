"""Resolve canonical Comment submissions against one immutable Review Snapshot."""

import hashlib
import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol

from agents import Tool, function_tool
from pydantic import Field, StringConstraints

from codelens.findings.domain.candidates import (
    CandidateFinding,
    CandidateFindingBatch,
    EvidenceStrength,
)
from codelens.findings.domain.models import FindingSeverity
from codelens.findings.infrastructure.comment_output import CommentFindingSchema
from codelens.review.application.settings import (
    MAX_MAX_INCOMPLETE_REVIEW_RETRIES,
    MIN_MAX_INCOMPLETE_REVIEW_RETRIES,
)
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.line_resolver import split_and_normalize
from codelens.review.infrastructure.location_resolver import (
    LocationOutsideChangedHunkError,
    SnapshotLocationResolver,
)
from codelens.review.infrastructure.tool_contract import reject_unknown_arguments
from codelens.workspace.domain.models import ReviewSnapshot


class CommentCandidateRejectedError(ValueError):
    """Report one semantically invalid candidate without rejecting its batch."""

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _EvidenceTools(Protocol):
    """Expose only bounded Snapshot operations needed for comment resolution."""

    @property
    def review_file_paths(self) -> tuple[str, ...]: ...

    @property
    def diff_viewed_paths(self) -> Collection[str]: ...

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
class ReviewCommentCollector:
    """Resolve Comment v2 items and enforce evidence coverage at task completion.

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
    review_feedback: str | None = None
    tool_limits: ToolLimits = field(default_factory=ToolLimits)
    max_incomplete_review_retries: int = 3
    _candidates: list[CandidateFinding] = field(default_factory=list, init=False)
    _completion_summary: str | None = field(default=None, init=False)
    _incomplete_retry_count: int = field(default=0, init=False)
    _incomplete_review_files: tuple[str, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.-]*:v[1-9][0-9]*", self.reviewer_reference) is None:
            raise ValueError("Comment v2 reviewer reference is invalid")
        if not self.reviewer_dimensions:
            raise ValueError("Comment v2 reviewer requires at least one assigned dimension")
        if len(self.reviewer_dimensions) != len(set(self.reviewer_dimensions)):
            raise ValueError("Comment v2 reviewer dimensions contain duplicates")
        retries = self.max_incomplete_review_retries
        if (
            isinstance(retries, bool)
            or retries < MIN_MAX_INCOMPLETE_REVIEW_RETRIES
            or retries > MAX_MAX_INCOMPLETE_REVIEW_RETRIES
        ):
            raise ValueError("max incomplete review retries must be between 0 and 20")

    async def submit(self, submission: CommentFindingSchema) -> str:
        """Resolve one submission or reject it without retaining partial state."""

        expected_reviewer_id = self.reviewer_reference.rpartition(":v")[0]
        if submission.reviewer_id != expected_reviewer_id:
            raise CommentCandidateRejectedError("comment reviewer does not match this Agent Run")
        if submission.primary_dimension not in self.reviewer_dimensions:
            raise CommentCandidateRejectedError(
                "comment primary dimension is outside this reviewer's assignment"
            )
        if submission.path not in self.tools.review_file_paths:
            raise CommentCandidateRejectedError("comment path is outside this Review")

        try:
            location, changed_hunk_id = await SnapshotLocationResolver(
                self.snapshot, self.tools
            ).resolve(
                submission.path,
                submission.side,
                submission.existing_code,
            )
        except LocationOutsideChangedHunkError as error:
            raise CommentCandidateRejectedError(
                self.review_feedback or str(error),
                reason_code="comment_outside_diff",
            ) from error
        except ValueError as error:
            raise CommentCandidateRejectedError(str(error)) from error
        existing_code_hash = _normalized_excerpt_hash(submission.existing_code)
        evidence_hashes = (existing_code_hash,)
        axes = {
            "evidence_strength": submission.evidence_strength,
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
                evidence_strength=EvidenceStrength(submission.evidence_strength),
                primary_location=location,
                related_locations=(),
                changed_hunk_id=changed_hunk_id,
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

    async def submit_many(self, submissions: list[CommentFindingSchema]) -> str:
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
                rejection: dict[str, object] = {"index": index, "reason": str(error)}
                if error.reason_code is not None:
                    rejection["reason_code"] = error.reason_code
                rejected_comments.append(rejection)
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

    def as_agent_tools(self, tool_descriptions: dict[str, str]) -> list[Tool]:
        """Expose the canonical comment and task completion contracts.

        The schema is mutated to inject the reviewer's assigned dimensions and
        reviewer identifier as enums so the model picks valid values instead of
        guessing free-form strings.
        """

        CommentBatch = Annotated[
            list[CommentFindingSchema],
            Field(min_length=1, max_length=self.tool_limits.comment_batch_size),
        ]

        @function_tool(name_override="comment", description_override=tool_descriptions["comment"])
        async def comment_tool(comments: CommentBatch) -> str:
            return await self.submit_many(comments)

        @function_tool(
            name_override="task_done", description_override=tool_descriptions["task_done"]
        )
        async def task_done_tool(
            summary: Annotated[
                str,
                StringConstraints(
                    strip_whitespace=True,
                    min_length=1,
                    max_length=self.tool_limits.task_summary_max,
                ),
            ],
        ) -> str:
            return self.complete(summary)

        tool = reject_unknown_arguments(comment_tool)
        expected_reviewer_id = self.reviewer_reference.rpartition(":v")[0]
        finding_schema = tool.params_json_schema.get("$defs", {}).get("CommentFindingSchema", {})
        properties = finding_schema.get("properties", {})
        properties["primary_dimension"]["enum"] = list(self.reviewer_dimensions)
        properties["reviewer_id"]["enum"] = [expected_reviewer_id]
        return [tool, reject_unknown_arguments(task_done_tool)]

    def complete(self, summary: str) -> str:
        """Accept task completion after all Review files have model-visible evidence."""

        if self._completion_summary is not None:
            raise ValueError("task_done has already been called")
        targets = set(self.tools.review_file_paths)
        viewed = set(self.tools.diff_viewed_paths)
        incomplete = tuple(sorted(targets - viewed))
        if incomplete:
            self._incomplete_retry_count += 1
            if self._incomplete_retry_count <= self.max_incomplete_review_retries:
                return json.dumps(
                    {
                        "accepted": False,
                        "incomplete_retry_count": self._incomplete_retry_count,
                        "max_incomplete_review_retries": self.max_incomplete_review_retries,
                        "missing_diff_files": incomplete,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            self._incomplete_review_files = incomplete
        self._completion_summary = summary
        return json.dumps(
            {
                "accepted": True,
                "comment_count": len(self._candidates),
                "forced_completion": bool(incomplete),
                **({"incomplete_files": incomplete} if incomplete else {}),
                "diff_viewed_files": tuple(sorted(targets & viewed)),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def is_completed(self) -> bool:
        """Return whether task_done was accepted."""

        return self._completion_summary is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        """Return paths missing evidence when forced completion was accepted."""

        return self._incomplete_review_files

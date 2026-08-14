"""Resolve canonical Comment submissions against one immutable Review Snapshot."""

import asyncio
import hashlib
import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, cast

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
from codelens.review.domain.tool_results import (
    JsonValue,
    ToolDiagnostic,
    ToolResult,
    ToolResultStatus,
)
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
    def reviewed_paths(self) -> Collection[str]: ...

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
class _CandidateRecord:
    """Retain one Candidate payload and its auditable active/retracted transition."""

    candidate: CandidateFinding
    business_key: str
    status: Literal["active", "retracted"] = "active"
    retraction_reason: str | None = None
    transitions: list[tuple[Literal["active", "retracted"], str | None]] = field(
        default_factory=lambda: [("active", None)]
    )


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
    _candidate_records: list[_CandidateRecord] = field(default_factory=list, init=False)
    _state_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
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

    async def submit(self, submission: CommentFindingSchema) -> CandidateFinding:
        """Resolve one submission or reject it without retaining partial state."""

        if self.is_completed:
            raise CommentCandidateRejectedError(
                "reviewer has already completed",
                reason_code="reviewer_already_completed",
            )

        expected_reviewer_id = self.reviewer_reference.rpartition(":v")[0]
        if submission.reviewer_id != expected_reviewer_id:
            raise CommentCandidateRejectedError(
                "comment reviewer does not match this Agent Run",
                reason_code="reviewer_mismatch",
            )
        if submission.primary_dimension not in self.reviewer_dimensions:
            raise CommentCandidateRejectedError(
                "comment primary dimension is outside this reviewer's assignment",
                reason_code="dimension_outside_assignment",
            )
        if submission.path not in self.tools.review_file_paths:
            raise CommentCandidateRejectedError(
                "comment path is outside this Review",
                reason_code="path_outside_review",
            )

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
        business_key = _canonical_hash(
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
        async with self._state_lock:
            if self.is_completed:
                raise CommentCandidateRejectedError(
                    "reviewer has already completed",
                    reason_code="reviewer_already_completed",
                )
            if any(
                record.business_key == business_key and record.status == "active"
                for record in self._candidate_records
            ):
                raise CommentCandidateRejectedError(
                    "comment duplicates an active candidate",
                    reason_code="duplicate_comment",
                )
            candidate_id = "candidate_" + _canonical_hash(
                {
                    "run_id": self.run_id,
                    "acceptance_index": len(self._candidate_records),
                    "business_key": business_key,
                }
            )
            candidate = CandidateFinding(
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
            self._candidate_records.append(
                _CandidateRecord(candidate=candidate, business_key=business_key)
            )
        return candidate

    async def submit_many(self, submissions: list[CommentFindingSchema]) -> str:
        """Retain valid submissions while reporting each rejected item by index."""

        if self.is_completed:
            return ToolResult(
                "comment",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "reviewer_already_completed",
                        "The reviewer has already completed this Agent Run.",
                        False,
                    ),
                ),
            ).to_json()
        if not submissions or len(submissions) > self.tool_limits.comment_batch_size:
            return ToolResult(
                "comment",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "invalid_argument_value",
                        "comment batch size is outside the configured limit.",
                        True,
                        "comments",
                    ),
                ),
            ).to_json()
        accepted_comments: list[dict[str, JsonValue]] = []
        rejected_comments: list[dict[str, JsonValue]] = []
        for input_index, submission in enumerate(submissions):
            try:
                candidate = await self.submit(submission)
            except CommentCandidateRejectedError as error:
                rejected_comments.append(
                    {
                        "input_index": input_index,
                        "code": error.reason_code or "comment_rejected",
                        "message": str(error),
                    }
                )
            else:
                accepted_comments.append(
                    {
                        "input_index": input_index,
                        "candidate_id": candidate.candidate_id,
                        "path": candidate.primary_location.path,
                        "side": candidate.primary_location.side,
                        "title": candidate.title,
                    }
                )
        accepted_count = len(accepted_comments)
        rejected_count = len(rejected_comments)
        status = (
            ToolResultStatus.SUCCESS
            if accepted_count and not rejected_count
            else (ToolResultStatus.PARTIAL if accepted_count else ToolResultStatus.REJECTED)
        )
        diagnostics = tuple(
            ToolDiagnostic(
                str(rejection["code"]),
                str(rejection["message"]),
                True,
            )
            for rejection in rejected_comments
        )
        return ToolResult(
            "comment",
            status,
            {
                "submitted_count": len(submissions),
                "accepted_count": accepted_count,
                "rejected_count": rejected_count,
                "active_comment_count": self.active_comment_count,
                "accepted_comments": cast(JsonValue, accepted_comments),
                "rejected_comments": cast(JsonValue, rejected_comments),
            },
            diagnostics,
        ).to_json()

    def retract_many(self, candidate_ids: list[str], reason: str) -> str:
        """Idempotently retract current-Run Candidates while preserving their audit records."""

        if self.is_completed:
            return ToolResult(
                "retract_comment",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "reviewer_already_completed",
                        "The reviewer has already completed this Agent Run.",
                        False,
                    ),
                ),
            ).to_json()
        normalized_reason = reason.strip()
        if (
            not candidate_ids
            or len(candidate_ids) > self.tool_limits.comment_batch_size
            or len(candidate_ids) != len(set(candidate_ids))
            or not normalized_reason
            or len(normalized_reason) > self.tool_limits.long_text_max
        ):
            return ToolResult(
                "retract_comment",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "invalid_argument_value",
                        "Retraction IDs and reason must satisfy the strict input limits.",
                        True,
                    ),
                ),
            ).to_json()
        records_by_id = {
            record.candidate.candidate_id: record for record in self._candidate_records
        }
        results: list[dict[str, JsonValue]] = []
        retracted_count = 0
        already_retracted_count = 0
        unknown_count = 0
        for candidate_id in candidate_ids:
            record = records_by_id.get(candidate_id)
            if record is None:
                item_status = "unknown_candidate"
                unknown_count += 1
            elif record.status == "retracted":
                item_status = "already_retracted"
                already_retracted_count += 1
            else:
                record.status = "retracted"
                record.retraction_reason = normalized_reason
                record.transitions.append(("retracted", normalized_reason))
                item_status = "retracted"
                retracted_count += 1
            results.append({"candidate_id": candidate_id, "status": item_status})
        status = (
            ToolResultStatus.REJECTED
            if unknown_count == len(candidate_ids)
            else (ToolResultStatus.PARTIAL if unknown_count else ToolResultStatus.SUCCESS)
        )
        diagnostics: tuple[ToolDiagnostic, ...] = ()
        if retracted_count == 0 and unknown_count == 0:
            diagnostics = (
                ToolDiagnostic(
                    "no_state_change",
                    "All requested Candidates were already retracted.",
                    False,
                ),
            )
        elif unknown_count:
            diagnostics = (
                ToolDiagnostic(
                    "unknown_candidate",
                    "At least one Candidate does not belong to this Reviewer Agent Run.",
                    False,
                    "candidate_ids",
                ),
            )
        return ToolResult(
            "retract_comment",
            status,
            {
                "results": cast(JsonValue, results),
                "retracted_count": retracted_count,
                "already_retracted_count": already_retracted_count,
                "unknown_count": unknown_count,
                "active_comment_count": self.active_comment_count,
            },
            diagnostics,
        ).to_json()

    def candidate_batch(self) -> CandidateFindingBatch:
        """Return only fully resolved candidates in stable acceptance order."""

        return CandidateFindingBatch(
            tuple(
                record.candidate for record in self._candidate_records if record.status == "active"
            )
        )

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
        CandidateIds = Annotated[
            list[
                Annotated[
                    str,
                    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
                ]
            ],
            Field(min_length=1, max_length=self.tool_limits.comment_batch_size),
        ]

        @function_tool(name_override="comment", description_override=tool_descriptions["comment"])
        async def comment_tool(comments: CommentBatch) -> str:
            return await self.submit_many(comments)

        @function_tool(
            name_override="retract_comment",
            description_override=tool_descriptions["retract_comment"],
        )
        async def retract_comment_tool(
            candidate_ids: CandidateIds,
            reason: Annotated[
                str,
                StringConstraints(
                    strip_whitespace=True,
                    min_length=1,
                    max_length=self.tool_limits.long_text_max,
                ),
            ],
        ) -> str:
            return self.retract_many(candidate_ids, reason)

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
        return [
            tool,
            reject_unknown_arguments(retract_comment_tool),
            reject_unknown_arguments(task_done_tool),
        ]

    def complete(self, summary: str) -> str:
        """Accept task completion after all Review files have model-visible evidence."""

        if self._completion_summary is not None:
            return ToolResult(
                "task_done",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "reviewer_already_completed",
                        "The reviewer has already completed this Agent Run.",
                        False,
                    ),
                ),
            ).to_json()
        targets = set(self.tools.review_file_paths)
        reviewed_targets = targets.intersection(self.tools.reviewed_paths)
        incomplete = tuple(sorted(targets - reviewed_targets))
        total_review_file_count = len(targets)
        reviewed_file_count = len(reviewed_targets)
        missing_file_count = len(incomplete)
        if incomplete:
            self._incomplete_retry_count += 1
            if self._incomplete_retry_count <= self.max_incomplete_review_retries:
                diagnostic_message = (
                    "Review evidence is still missing for one or more files."
                    if self._incomplete_retry_count == 1
                    else (
                        "Review evidence is still missing. Do not call task_done again "
                        "until every missing_review_files entry has evidence."
                    )
                )
                return ToolResult(
                    "task_done",
                    ToolResultStatus.NEEDS_ACTION,
                    {
                        "incomplete_retry_count": self._incomplete_retry_count,
                        "max_incomplete_review_retries": self.max_incomplete_review_retries,
                        "missing_review_files": cast(JsonValue, list(incomplete)),
                        "missing_file_count": missing_file_count,
                        "reviewed_file_count": reviewed_file_count,
                        "total_review_file_count": total_review_file_count,
                        "active_comment_count": self.active_comment_count,
                    },
                    (
                        ToolDiagnostic(
                            "missing_review_files",
                            diagnostic_message,
                            True,
                        ),
                    ),
                ).to_json()
            self._incomplete_review_files = incomplete
        self._completion_summary = summary
        return ToolResult(
            "task_done",
            ToolResultStatus.SUCCESS,
            {
                "active_comment_count": self.active_comment_count,
                "forced_completion": bool(incomplete),
                "incomplete_files": cast(JsonValue, list(incomplete)),
                "missing_file_count": missing_file_count,
                "reviewed_file_count": reviewed_file_count,
                "total_review_file_count": total_review_file_count,
            },
        ).to_json()

    @property
    def is_completed(self) -> bool:
        """Return whether task_done was accepted."""

        return self._completion_summary is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        """Return paths missing evidence when forced completion was accepted."""

        return self._incomplete_review_files

    @property
    def active_comment_count(self) -> int:
        """Return the number of Candidates eligible for final publication."""

        return sum(record.status == "active" for record in self._candidate_records)

    @property
    def candidate_audit(
        self,
    ) -> tuple[
        tuple[
            str,
            tuple[tuple[Literal["active", "retracted"], str | None], ...],
        ],
        ...,
    ]:
        """Expose bounded Candidate state transitions for tests and transcript projection."""

        return tuple(
            (
                record.candidate.candidate_id,
                tuple(record.transitions),
            )
            for record in self._candidate_records
        )

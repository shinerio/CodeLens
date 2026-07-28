"""Collect and resolve model review comments against one immutable Snapshot."""

import json
from dataclasses import dataclass, field
from typing import Annotated, Literal

from agents import Tool, function_tool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, create_model

from codelens.review.application.settings import (
    MAX_MAX_INCOMPLETE_REVIEW_RETRIES,
    MIN_MAX_INCOMPLETE_REVIEW_RETRIES,
)
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.line_resolver import (
    resolve_from_file_content,
    resolve_from_hunk,
)
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.review.infrastructure.tool_contract import reject_unknown_arguments
from codelens.workspace.domain.models import ReviewSnapshot

_DEFAULT_LIMITS = ToolLimits()

_ShortText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=_DEFAULT_LIMITS.short_text_max
    ),
]
_ReviewPath = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=_DEFAULT_LIMITS.max_path_chars
    ),
]
_LongText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=_DEFAULT_LIMITS.long_text_max
    ),
]

ReviewCommentSubmission = create_model(
    "ReviewCommentSubmission",
    __config__=ConfigDict(frozen=True, extra="forbid"),
    path=(_ReviewPath, ...),
    side=(Literal["old", "new"], ...),
    existing_code=(_LongText, ...),
    title=(_ShortText, ...),
    content=(_LongText, ...),
    recommendation=(_LongText, ...),
    category=(_ShortText, ...),
    severity=(Literal["critical", "high", "medium", "low", "info"], ...),
    confidence=(Annotated[float, Field(ge=0.0, le=1.0)], ...),
)

ReviewCompletionSubmission = create_model(
    "ReviewCompletionSubmission",
    __config__=ConfigDict(frozen=True, extra="forbid"),
    summary=(
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=_DEFAULT_LIMITS.task_summary_max,
            ),
        ],
        ...,
    ),
)

ReviewFileCompletionSubmission = create_model(
    "ReviewFileCompletionSubmission",
    __config__=ConfigDict(frozen=True, extra="forbid"),
    reviewed_files=(
        Annotated[
            list[_ReviewPath],
            Field(min_length=1, max_length=_DEFAULT_LIMITS.reviewed_files_batch),
        ],
        ...,
    ),
)


class CommentCandidateRejectedError(ValueError):
    """Report one semantically invalid candidate without rejecting its batch."""


@dataclass
class ReviewCommentCollector:
    """Task-local stateful tool that resolves accepted comments into Finding candidates.

    The collector has no persistence, workspace, network, or arbitrary-process access.
    It accepts comments only when their complete selected-side range is inside one
    frozen changed hunk, then derives trusted location metadata from that Snapshot.
    """

    snapshot: ReviewSnapshot
    reviewer_id: str
    confidence_floor: float
    tools: FilesystemReviewTools
    max_incomplete_review_retries: int = 3
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    tool_limits: ToolLimits | None = None
    _findings: list[dict[str, object]] = field(default_factory=list)
    _completion: object | None = None
    _reviewed_files: set[str] = field(default_factory=set)
    _incomplete_retry_count: int = 0
    _incomplete_review_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject invalid retry policies even when constructed outside a composition root."""

        value = self.max_incomplete_review_retries
        if (
            isinstance(value, bool)
            or value < MIN_MAX_INCOMPLETE_REVIEW_RETRIES
            or value > MAX_MAX_INCOMPLETE_REVIEW_RETRIES
        ):
            raise ValueError("max incomplete review retries must be between 0 and 20")
        if self.tool_limits is None:
            self.tool_limits = ToolLimits()

    def as_agent_tools(self) -> list[Tool]:
        """Expose bounded comment collection and explicit completion through the SDK."""

        limits = self.tool_limits if self.tool_limits is not None else ToolLimits()

        ShortText = Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=limits.short_text_max
            ),
        ]
        ReviewPath = Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=limits.max_path_chars
            ),
        ]
        LongText = Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=limits.long_text_max
            ),
        ]

        ReviewCommentSubmissionModel = create_model(  # type: ignore[misc]
            "ReviewCommentSubmission",
            __config__=ConfigDict(frozen=True, extra="forbid"),
            path=(ReviewPath, ...),
            side=(Literal["old", "new"], ...),
            existing_code=(LongText, ...),
            title=(ShortText, ...),
            content=(LongText, ...),
            recommendation=(LongText, ...),
            category=(ShortText, ...),
            severity=(Literal["critical", "high", "medium", "low", "info"], ...),
            confidence=(Annotated[float, Field(ge=0.0, le=1.0)], ...),
        )

        ReviewCompletionSubmissionModel = create_model(
            "ReviewCompletionSubmission",
            __config__=ConfigDict(frozen=True, extra="forbid"),
            summary=(
                Annotated[
                    str,
                    StringConstraints(
                        strip_whitespace=True,
                        min_length=1,
                        max_length=limits.task_summary_max,
                    ),
                ],
                ...,
            ),
        )

        ReviewFileCompletionSubmissionModel = create_model(
            "ReviewFileCompletionSubmission",
            __config__=ConfigDict(frozen=True, extra="forbid"),
            reviewed_files=(
                Annotated[
                    list[ReviewPath],
                    Field(min_length=1, max_length=limits.reviewed_files_batch),
                ],
                ...,
            ),
        )

        CommentBatch = Annotated[
            list[ReviewCommentSubmissionModel],  # type: ignore[valid-type]
            Field(min_length=1, max_length=limits.comment_batch_size),
        ]

        @function_tool(
            name_override="comment",
            description_override=self.tool_descriptions["comment"],
        )
        async def comment_tool(comments: CommentBatch) -> str:
            """Submit one or more concrete changed-code comments for deterministic resolution."""

            return await self.submit_many(comments)

        @function_tool(
            name_override="review_file_done",
            description_override=self.tool_descriptions["review_file_done"],
        )
        async def review_file_done_tool(
            reviewed_files: Annotated[
                list[ReviewPath],
                Field(min_length=1, max_length=limits.reviewed_files_batch),
            ],
        ) -> str:
            """Record files reviewed after successful model-visible evidence access."""

            return self.complete_files(
                ReviewFileCompletionSubmissionModel.model_validate(
                    {"reviewed_files": reviewed_files}
                )
            )

        @function_tool(
            name_override="task_done",
            description_override=self.tool_descriptions["task_done"],
        )
        async def task_done_tool(
            summary: Annotated[
                str,
                StringConstraints(
                    strip_whitespace=True,
                    min_length=1,
                    max_length=limits.task_summary_max,
                ),
            ],
        ) -> str:
            """Declare that changed-file investigation is complete without creating a Finding."""

            return self.complete(
                ReviewCompletionSubmissionModel.model_validate({"summary": summary})
            )

        return [
            reject_unknown_arguments(comment_tool),
            reject_unknown_arguments(review_file_done_tool),
            reject_unknown_arguments(task_done_tool),
        ]

    async def submit(self, submission: BaseModel) -> str:
        """Resolve one candidate or return a bounded tool error without retaining it."""

        if submission.confidence < self.confidence_floor:  # type: ignore[attr-defined]
            raise CommentCandidateRejectedError(
                "comment confidence is below this reviewer's threshold"
            )
        if submission.path not in self.tools.review_file_paths:  # type: ignore[attr-defined]
            raise CommentCandidateRejectedError("comment path is outside this Review")

        # Resolve line numbers from quoted code
        start_line, end_line = await self._resolve_line_numbers(
            submission.path, submission.existing_code, submission.side  # type: ignore[attr-defined]
        )

        hunks = tuple(
            hunk
            for hunk in self.snapshot.change_index.hunks
            if (
                hunk.path == submission.path  # type: ignore[attr-defined]
                and hunk.side == submission.side  # type: ignore[attr-defined]
                and start_line >= hunk.start_line
                and end_line <= hunk.end_line
            )
        )
        if len(hunks) != 1:
            raise CommentCandidateRejectedError(
                f"existing_code must quote only consecutive changed {submission.side}-side "  # type: ignore[attr-defined]
                "lines without diff markers; do not include unchanged context lines"
            )
        excerpt_hash, excerpt_truncated = await self.tools.excerpt_identity(
            submission.path,  # type: ignore[attr-defined]
            start_line,
            end_line,
            "base" if submission.side == "old" else "current",  # type: ignore[attr-defined]
        )
        if excerpt_truncated:
            raise CommentCandidateRejectedError(
                "comment location cannot be resolved to a complete frozen excerpt"
            )
        hunk = hunks[0]
        self._findings.append(
            {
                "reviewer_id": self.reviewer_id,
                "category": submission.category,  # type: ignore[attr-defined]
                "title": submission.title,  # type: ignore[attr-defined]
                "severity": submission.severity,  # type: ignore[attr-defined]
                "disposition": (
                    "blocking"
                    if submission.severity in {"critical", "high", "medium"}  # type: ignore[attr-defined]
                    else "non_blocking"
                ),
                "confidence": submission.confidence,  # type: ignore[attr-defined]
                "primary_location": {
                    "path": submission.path,  # type: ignore[attr-defined]
                    "start_line": start_line,
                    "end_line": end_line,
                    "side": submission.side,  # type: ignore[attr-defined]
                    "excerpt_hash": excerpt_hash,
                    "is_deleted": self._is_deleted_path(submission.path),  # type: ignore[attr-defined]
                },
                "changed_hunk_id": hunk.hunk_id,
                "change_origin": "introduced",
                "evidence": (
                    {
                        "kind": "excerpt",
                        "description": submission.content,  # type: ignore[attr-defined]
                        "excerpt_hash": excerpt_hash,
                    },
                ),
                "impact": submission.content,  # type: ignore[attr-defined]
                "explanation": submission.content,  # type: ignore[attr-defined]
                "recommendation": submission.recommendation,  # type: ignore[attr-defined]
            }
        )
        return json.dumps(
            {"accepted": True, "comment_count": len(self._findings)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def submit_many(self, submissions: list[BaseModel]) -> str:
        """Resolve a bounded batch while retaining only individually accepted comments."""

        if not submissions or len(submissions) > self.tool_limits.comment_batch_size:  # type: ignore[union-attr]
            raise ValueError(
                f"comment requires between one and {self.tool_limits.comment_batch_size} comments"  # type: ignore[union-attr]
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
                "comment_count": len(self._findings),
                "rejected_comments": rejected_comments,
                "rejected_count": len(rejected_comments),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def complete(self, submission: BaseModel) -> str:
        """Accept final completion only after every evidenced Review file was declared."""

        if self._completion is not None:
            raise ValueError("task_done has already been called")
        targets = set(self.tools.review_file_paths)
        viewed = set(self.tools.evidence_viewed_paths)
        missing_evidence = tuple(sorted(targets - viewed))
        undeclared = tuple(sorted((targets & viewed) - self._reviewed_files))
        incomplete = tuple(sorted(targets - self._reviewed_files))
        if incomplete:
            self._incomplete_retry_count += 1
            if self._incomplete_retry_count <= self.max_incomplete_review_retries:
                return json.dumps(
                    {
                        "accepted": False,
                        "incomplete_retry_count": self._incomplete_retry_count,
                        "max_incomplete_review_retries": self.max_incomplete_review_retries,
                        "missing_evidence_files": missing_evidence,
                        "undeclared_files": undeclared,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            self._incomplete_review_files = incomplete
        self._completion = submission
        return json.dumps(
            {
                "accepted": True,
                "comment_count": len(self._findings),
                "forced_completion": bool(incomplete),
                **({"incomplete_files": incomplete} if incomplete else {}),
                "reviewed_files": tuple(sorted(self._reviewed_files)),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def complete_files(self, submission: BaseModel) -> str:
        """Record only known paths already exposed by a successful evidence tool call."""

        if self._completion is not None:
            raise ValueError("review task has already been completed")
        targets = set(self.tools.review_file_paths)
        requested = set(submission.reviewed_files)  # type: ignore[attr-defined]
        unknown = tuple(sorted(requested - targets))
        if unknown:
            raise ValueError(f"reviewed_files contains paths outside this Review: {unknown[0]}")
        missing_evidence = tuple(sorted(requested - self.tools.evidence_viewed_paths))
        recorded = tuple(sorted(requested - set(missing_evidence)))
        self._reviewed_files.update(recorded)
        return json.dumps(
            {
                "accepted": not missing_evidence,
                "missing_evidence_files": missing_evidence,
                "recorded_files": recorded,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def is_completed(self) -> bool:
        """Return whether the model explicitly ended this investigation."""

        return self._completion is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        """Return paths left incomplete when the configured retry limit was exceeded."""

        return self._incomplete_review_files

    async def _resolve_line_numbers(
        self,
        path: str,
        existing_code: str,
        side: Literal["old", "new"],
    ) -> tuple[int, int]:
        """Resolve line numbers from quoted code via diff hunk or file content matching."""

        # Tier 1: Try hunk matching
        diff_result = json.loads(await self.tools.read_diff_for_resolution(path))
        diff_text = diff_result.get("content", "")
        resolved = resolve_from_hunk(diff_text, existing_code, side=side)
        if resolved is not None:
            return resolved

        # Tier 2: Try full file content matching
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
        entry = next((item for item in self.snapshot.manifest.entries if item.path == path), None)
        if entry is None:
            raise ValueError("comment path is outside the frozen Snapshot")
        return entry.kind == "deleted"

    def finding_batch(self) -> dict[str, object]:
        """Return only resolved candidates in the stable output envelope."""

        return {"schema_version": "1", "findings": tuple(self._findings)}

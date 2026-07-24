"""Collect and resolve model review comments against one immutable Snapshot."""

import json
from dataclasses import dataclass, field
from typing import Annotated, Literal

from agents import Tool, function_tool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.workspace.domain.models import ReviewSnapshot

_ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
_LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000)]


class ReviewCommentSubmission(BaseModel):
    """Validate model-supplied comment fields before Snapshot resolution.

    The model is deliberately not allowed to provide hunk IDs, hashes, or trusted
    locations. Those values are derived from the task's frozen Snapshot only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: _ShortText
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    title: _ShortText
    content: _LongText
    recommendation: _LongText
    category: _ShortText = "correctness"
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class ReviewCompletionSubmission(BaseModel):
    """Validate the model's bounded review-completion declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: _LongText
    reviewed_changed_files: int = Field(ge=0, le=10_000)


@dataclass
class ReviewCommentCollector:
    """Task-local stateful tool that resolves accepted comments into Finding candidates.

    The collector has no persistence, workspace, network, or arbitrary-process access.
    It accepts comments only when their complete new-side range is inside one frozen
    changed hunk, then derives the hunk ID and excerpt hash from that Snapshot.
    """

    snapshot: ReviewSnapshot
    reviewer_id: str
    confidence_floor: float
    tools: FilesystemReviewTools
    output_locale: Literal["en", "zh-CN"] = "en"
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    language_error: str = "Chinese review output is required for this task"
    _findings: list[dict[str, object]] = field(default_factory=list)
    _completion: ReviewCompletionSubmission | None = None

    def as_agent_tools(self) -> list[Tool]:
        """Expose bounded comment collection and explicit completion through the SDK."""

        @function_tool(
            name_override="comment",
            description_override=self.tool_descriptions["comment"],
        )
        async def comment_tool(comments: list[ReviewCommentSubmission]) -> str:
            """Submit one or more concrete changed-code comments for deterministic resolution."""

            return await self.submit_many(comments)

        @function_tool(
            name_override="task_done",
            description_override=self.tool_descriptions["task_done"],
        )
        async def task_done_tool(summary: str, reviewed_changed_files: int) -> str:
            """Declare that changed-file investigation is complete without creating a Finding."""

            return self.complete(
                ReviewCompletionSubmission.model_validate(
                    {"summary": summary, "reviewed_changed_files": reviewed_changed_files}
                )
            )

        return [comment_tool, task_done_tool]

    async def submit(self, submission: ReviewCommentSubmission) -> str:
        """Resolve one candidate or return a bounded tool error without retaining it."""

        if submission.end_line < submission.start_line:
            raise ValueError("comment line range is invalid")
        self._validate_output_language(
            submission.title,
            submission.content,
            submission.recommendation,
        )
        if submission.confidence < self.confidence_floor:
            raise ValueError("comment confidence is below this reviewer's threshold")
        hunks = tuple(
            hunk
            for hunk in self.snapshot.change_index.hunks
            if (
                hunk.path == submission.path
                and hunk.side == "new"
                and submission.start_line >= hunk.start_line
                and submission.end_line <= hunk.end_line
            )
        )
        if len(hunks) != 1:
            raise ValueError("comment must be fully contained in exactly one changed new-side hunk")
        excerpt = json.loads(
            await self.tools.read_file(submission.path, submission.start_line, submission.end_line)
        )
        if not isinstance(excerpt, dict) or excerpt.get("truncated") is True:
            raise ValueError("comment location cannot be resolved to a complete frozen excerpt")
        excerpt_hash = excerpt.get("content_hash")
        if not isinstance(excerpt_hash, str):
            raise ValueError("comment location has no frozen excerpt hash")
        hunk = hunks[0]
        self._findings.append(
            {
                "reviewer_id": self.reviewer_id,
                "category": submission.category,
                "title": submission.title,
                "severity": submission.severity,
                "disposition": (
                    "blocking"
                    if submission.severity in {"critical", "high", "medium"}
                    else "non_blocking"
                ),
                "confidence": submission.confidence,
                "primary_location": {
                    "path": submission.path,
                    "start_line": submission.start_line,
                    "end_line": submission.end_line,
                    "side": "new",
                    "excerpt_hash": excerpt_hash,
                },
                "changed_hunk_id": hunk.hunk_id,
                "change_origin": "introduced",
                "evidence": (
                    {
                        "kind": "excerpt",
                        "description": submission.content,
                        "excerpt_hash": excerpt_hash,
                    },
                ),
                "impact": submission.content,
                "explanation": submission.content,
                "recommendation": submission.recommendation,
            }
        )
        return json.dumps(
            {"accepted": True, "comment_count": len(self._findings), "hunk_id": hunk.hunk_id},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def submit_many(self, submissions: list[ReviewCommentSubmission]) -> str:
        """Resolve a bounded batch while retaining only individually accepted comments."""

        if not submissions or len(submissions) > 20:
            raise ValueError("comment requires between one and twenty comments")
        accepted_hunks: list[str] = []
        for submission in submissions:
            acknowledgement = json.loads(await self.submit(submission))
            hunk_id = acknowledgement.get("hunk_id")
            if not isinstance(hunk_id, str):
                raise ValueError("comment acknowledgement is invalid")
            accepted_hunks.append(hunk_id)
        return json.dumps(
            {
                "accepted": True,
                "accepted_count": len(accepted_hunks),
                "comment_count": len(self._findings),
                "hunk_ids": accepted_hunks,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def complete(self, submission: ReviewCompletionSubmission) -> str:
        """Record one task-local completion declaration and return trusted aggregate counts."""

        if self._completion is not None:
            raise ValueError("task_done has already been called")
        self._validate_output_language(submission.summary)
        self._completion = submission
        return json.dumps(
            {
                "accepted": True,
                "comment_count": len(self._findings),
                "reviewed_changed_files": submission.reviewed_changed_files,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def is_completed(self) -> bool:
        """Return whether the model explicitly ended this investigation."""

        return self._completion is not None

    def _validate_output_language(self, *values: str) -> None:
        """Reject non-Chinese user-facing output when this run selected Chinese.

        Code identifiers may remain in English, but each submitted user-facing field
        must include Chinese prose. A rejected tool call lets the model correct its
        own response without allowing an English Finding into a Chinese review.
        """

        if self.output_locale != "zh-CN":
            return
        if any(
            not any("\u4e00" <= character <= "\u9fff" for character in value) for value in values
        ):
            raise ValueError(self.language_error)

    def finding_batch(self) -> dict[str, object]:
        """Return only resolved candidates in the stable output envelope."""

        return {"schema_version": "1", "findings": tuple(self._findings)}

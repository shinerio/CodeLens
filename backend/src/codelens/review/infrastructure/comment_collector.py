"""Collect and resolve model review comments against one immutable Snapshot."""

import json
from dataclasses import dataclass, field
from typing import Annotated, Literal

from agents import Tool, function_tool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from codelens.review.infrastructure.line_resolver import (
    resolve_from_file_content,
    resolve_from_hunk,
)
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.workspace.domain.models import ReviewSnapshot

_ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
_LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000)]


class ReviewCommentSubmission(BaseModel):
    """Validate one evidence-backed Review comment submission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: _ShortText
    existing_code: _LongText
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
    tool_descriptions: dict[str, str] = field(default_factory=dict)
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

        if not self.tools.instructions_loaded_for(submission.path):
            raise ValueError(
                "instruction_loader must be called for the complete target path before comment"
            )
        if submission.confidence < self.confidence_floor:
            raise ValueError("comment confidence is below this reviewer's threshold")

        # Resolve line numbers from quoted code
        start_line, end_line = await self._resolve_line_numbers(
            submission.path, submission.existing_code
        )

        hunks = tuple(
            hunk
            for hunk in self.snapshot.change_index.hunks
            if (
                hunk.path == submission.path
                and hunk.side == "new"
                and start_line >= hunk.start_line
                and end_line <= hunk.end_line
            )
        )
        if len(hunks) != 1:
            raise ValueError(
                "existing_code must be fully contained in exactly one changed new-side hunk"
            )
        excerpt_hash, excerpt_truncated = await self.tools.excerpt_identity(
            submission.path,
            start_line,
            end_line,
        )
        if excerpt_truncated:
            raise ValueError("comment location cannot be resolved to a complete frozen excerpt")
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
                    "start_line": start_line,
                    "end_line": end_line,
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
            {"accepted": True, "comment_count": len(self._findings)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def submit_many(self, submissions: list[ReviewCommentSubmission]) -> str:
        """Resolve a bounded batch while retaining only individually accepted comments."""

        if not submissions or len(submissions) > 20:
            raise ValueError("comment requires between one and twenty comments")
        for submission in submissions:
            await self.submit(submission)
        return json.dumps(
            {
                "accepted": True,
                "accepted_count": len(submissions),
                "comment_count": len(self._findings),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def complete(self, submission: ReviewCompletionSubmission) -> str:
        """Record one task-local completion declaration and return trusted aggregate counts."""

        if self._completion is not None:
            raise ValueError("task_done has already been called")
        if self.tools.unloaded_instruction_paths:
            raise ValueError(
                "instruction_loader must be called for every changed target before task_done"
            )
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

    async def _resolve_line_numbers(self, path: str, existing_code: str) -> tuple[int, int]:
        """Resolve line numbers from quoted code via diff hunk or file content matching."""

        # Tier 1: Try hunk matching
        diff_result = json.loads(await self.tools.get_diff(path))
        diff_text = diff_result.get("content", "")
        resolved = resolve_from_hunk(diff_text, existing_code)
        if resolved is not None:
            return resolved

        # Tier 2: Try full file content matching
        file_content = await self.tools.read_full_file(path)
        resolved = resolve_from_file_content(file_content, existing_code)
        if resolved is not None:
            return resolved

        raise ValueError("existing_code cannot be resolved to a line range")

    def finding_batch(self) -> dict[str, object]:
        """Return only resolved candidates in the stable output envelope."""

        return {"schema_version": "1", "findings": tuple(self._findings)}

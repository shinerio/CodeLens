from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from codelens.review.domain.ports import ReviewRecord
from codelens.workspace.domain.models import (
    BranchScope,
    CommitScope,
    FullRepositoryScope,
    ReviewScope,
    UncommittedScope,
)
from codelens.workspace.domain.ports import RepositoryInfo


class StrictDto(BaseModel):
    """Reject unknown public fields so clients cannot inject internal identifiers."""

    model_config = ConfigDict(extra="forbid")


RefLabel = Annotated[str, StringConstraints(min_length=1, max_length=512)]
AgentReference = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]


class BranchScopeRequest(StrictDto):
    type: Literal["branch"]
    base_ref: RefLabel
    target_ref: RefLabel
    include_workspace_changes: bool = False

    def to_domain(self) -> ReviewScope:
        return BranchScope(
            base_ref=self.base_ref,
            target_ref=self.target_ref,
            include_workspace_changes=self.include_workspace_changes,
        )


class CommitScopeRequest(StrictDto):
    type: Literal["commit"]
    base_commit: RefLabel
    target_ref: RefLabel = "HEAD"
    include_workspace_changes: bool = False

    def to_domain(self) -> ReviewScope:
        return CommitScope(
            base_commit=self.base_commit,
            target_ref=self.target_ref,
            include_workspace_changes=self.include_workspace_changes,
        )


class UncommittedScopeRequest(StrictDto):
    type: Literal["uncommitted"]

    def to_domain(self) -> ReviewScope:
        return UncommittedScope()


class FullRepositoryScopeRequest(StrictDto):
    type: Literal["full"]
    target_ref: RefLabel = "HEAD"
    include_workspace_changes: bool = False

    def to_domain(self) -> ReviewScope:
        return FullRepositoryScope(
            target_ref=self.target_ref,
            include_workspace_changes=self.include_workspace_changes,
        )


ScopeRequest = Annotated[
    BranchScopeRequest | CommitScopeRequest | UncommittedScopeRequest | FullRepositoryScopeRequest,
    Field(discriminator="type"),
]


class RepositoryInspectionRequest(StrictDto):
    path: Path


class RepositoryResponse(StrictDto):
    repository_id: str
    repository_realpath_hash: str
    git_common_dir_hash: str
    display_path: str
    head_oid: str
    current_branch: str | None
    is_dirty: bool

    @classmethod
    def from_domain(cls, repository: RepositoryInfo) -> "RepositoryResponse":
        return cls(
            repository_id=repository.repository_id,
            repository_realpath_hash=repository.repository_realpath_hash,
            git_common_dir_hash=repository.git_common_dir_hash,
            display_path=str(repository.path),
            head_oid=repository.head_sha,
            current_branch=repository.current_branch,
            is_dirty=repository.is_dirty,
        )


class CreateReviewRequest(StrictDto):
    repository_path: Path
    scope: ScopeRequest
    selected_agents: Annotated[list[AgentReference], Field(min_length=1, max_length=32)]
    mode: Literal["review"] = "review"


class ReviewResponse(StrictDto):
    task_id: str
    status: str
    scope_type: str
    base_oid: str
    head_oid: str
    selected_agents: list[str]
    worktree_status: Literal["pending"] = "pending"
    repository_id: str
    repository_realpath_hash: str
    git_common_dir_hash: str
    cancellation_requested: bool

    @classmethod
    def from_domain(cls, review: ReviewRecord) -> "ReviewResponse":
        return cls(
            task_id=review.task_id,
            status=review.status,
            scope_type=review.scope_type,
            base_oid=review.base_oid,
            head_oid=review.head_oid,
            selected_agents=list(review.selected_agent_versions),
            repository_id=review.repository_id,
            repository_realpath_hash=review.repository_realpath_hash,
            git_common_dir_hash=review.git_common_dir_hash,
            cancellation_requested=review.cancellation_requested,
        )


class CancelReviewRequest(StrictDto):
    pass


class ProblemResponse(StrictDto):
    code: str
    message: str

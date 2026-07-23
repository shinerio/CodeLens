from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)

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


class FindingSourcePreviewResponse(StrictDto):
    path: str
    revision: str
    start_line: int
    end_line: int
    highlight_start_line: int
    highlight_end_line: int
    content: str


class RuntimeLogLevelResponse(StrictDto):
    level: Literal["debug", "info", "warning", "error"]


class UpdateRuntimeLogLevelRequest(RuntimeLogLevelResponse):
    pass


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


class RepositoryCatalogRequest(StrictDto):
    """Request selectable refs for one validated exact repository root."""

    path: Path
    commit_offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    commit_limit: Annotated[int, Field(ge=1, le=50)] = 10


class RepositoryBranchResponse(StrictDto):
    """Expose one local or remote branch option."""

    name: str
    oid: str
    is_current: bool
    is_remote: bool


class RepositoryCommitResponse(StrictDto):
    """Expose one bounded recent-commit option."""

    oid: str
    short_oid: str
    author: str
    message: str
    committed_at: str


class RepositoryCatalogResponse(StrictDto):
    """Expose branch options and a paginated commit summary page."""

    branches: list[RepositoryBranchResponse]
    commits: list[RepositoryCommitResponse]
    next_commit_offset: int | None


class DirectoryBrowseRequest(StrictDto):
    """Request system roots or the children of one absolute local directory."""

    path: Path | None = None


class DirectoryEntryResponse(StrictDto):
    """Expose one directory selectable in the local resource browser."""

    name: str
    path: str
    is_git_repository: bool


class DirectoryListingResponse(StrictDto):
    """Expose a bounded directory-only listing and all platform roots."""

    current_path: str | None
    parent_path: str | None
    roots: list[str]
    directories: list[DirectoryEntryResponse]
    current_is_git_repository: bool
    is_truncated: bool


class CreateReviewRequest(StrictDto):
    repository_path: Path
    scope: ScopeRequest
    selected_agents: Annotated[list[AgentReference], Field(min_length=1, max_length=32)]
    mode: Literal["review"] = "review"
    prompt_locale: Literal["en", "zh-CN"] = "en"


class UpdateReviewerPromptRequest(StrictDto):
    prompt: Annotated[str, Field(min_length=1, max_length=100_000)]


class ReviewerPromptResponse(StrictDto):
    agent_id: str
    version: int
    locale: Literal["en", "zh-CN"]
    system_prompt: str
    prompt: str
    is_custom: bool


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
    repository_name: str
    created_at: datetime

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
            repository_name=review.repository_name,
            created_at=review.created_at,
        )


class CancelReviewRequest(StrictDto):
    pass


class ProblemResponse(StrictDto):
    code: str
    message: str


class UpdateOpenAISettingsRequest(StrictDto):
    """Validate one complete write-only OpenAI-compatible provider configuration."""

    api_key: SecretStr
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    base_url: AnyHttpUrl

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        """Reject empty credentials and normalize accidental surrounding whitespace."""

        normalized = value.get_secret_value().strip()
        if not normalized:
            raise ValueError("api_key must not be empty")
        return SecretStr(normalized)


class OpenAISettingsResponse(StrictDto):
    """Expose provider readiness without ever serializing the API key."""

    is_configured: bool
    model: str | None
    base_url: str | None


GatewayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
GatewayModel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class CreateModelGatewayRequest(StrictDto):
    """Validate one new named gateway while keeping its API key write-only."""

    name: GatewayName
    api_key: SecretStr
    model: GatewayModel
    base_url: AnyHttpUrl
    vendor: Literal["openai", "deepseek"] = "openai"
    api_type: Literal["responses", "chat_completions"] = "chat_completions"
    max_tokens: int = 65536
    thinking_level: Literal["disabled", "low", "medium", "high"] = "disabled"
    agent_timeout: int = 1800

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        normalized = value.get_secret_value().strip()
        if not normalized:
            raise ValueError("api_key must not be empty")
        return SecretStr(normalized)


class UpdateModelGatewayRequest(StrictDto):
    """Replace gateway metadata and optionally rotate its write-only API key."""

    name: GatewayName
    api_key: SecretStr | None = None
    model: GatewayModel
    base_url: AnyHttpUrl
    vendor: Literal["openai", "deepseek"] = "openai"
    api_type: Literal["responses", "chat_completions"] = "chat_completions"
    max_tokens: int = 65536
    thinking_level: Literal["disabled", "low", "medium", "high"] = "disabled"
    agent_timeout: int = 1800

    @field_validator("api_key")
    @classmethod
    def validate_optional_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        normalized = value.get_secret_value().strip()
        if not normalized:
            raise ValueError("api_key must not be empty")
        return SecretStr(normalized)


class ActivateModelGatewayRequest(StrictDto):
    """Select one persistent gateway for subsequent model invocations."""

    gateway_id: Annotated[
        str,
        StringConstraints(pattern=r"^gateway_[A-Za-z0-9_-]{3,64}$", max_length=72),
    ]


class ModelGatewayResponse(StrictDto):
    """Expose redacted gateway metadata."""

    gateway_id: str
    name: str
    model: str
    base_url: str
    vendor: Literal["openai", "deepseek"]
    is_active: bool
    api_type: Literal["responses", "chat_completions"]
    max_tokens: int
    thinking_level: Literal["disabled", "low", "medium", "high"]
    agent_timeout: int


class ModelGatewayCatalogResponse(StrictDto):
    """Expose the redacted ordered gateway catalog and active selection."""

    active_gateway_id: str | None
    gateways: list[ModelGatewayResponse]


class GatewayConnectivityTestResponse(StrictDto):
    """Report whether the gateway base URL accepts TCP connections."""

    ok: bool
    latency_ms: int | None
    detail: str


class GatewayAvailabilityTestResponse(StrictDto):
    """Report whether the LLM endpoint responds to a minimal ping."""

    ok: bool
    latency_ms: int | None
    detail: str

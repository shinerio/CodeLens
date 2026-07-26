from dataclasses import asdict
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
    model_validator,
)

from codelens.review.application.process_report import ReviewProcessReport
from codelens.review.domain.ports import RecentRepositoryRecord, ReviewRecord
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


class PinnedSourceVersionResponse(StrictDto):
    path: str
    revision: str
    content: str


class FindingSourcePreviewResponse(StrictDto):
    path: str
    base: PinnedSourceVersionResponse | None
    target: PinnedSourceVersionResponse | None
    highlight_side: Literal["old", "new"]
    highlight_start_line: int
    highlight_end_line: int


class RuntimeLogLevelResponse(StrictDto):
    level: Literal["debug", "info", "warning", "error"]


class UpdateRuntimeLogLevelRequest(RuntimeLogLevelResponse):
    pass


class RecentRepositorySettingsResponse(StrictDto):
    recent_repository_limit: Annotated[int, Field(ge=1, le=20)]


class UpdateRecentRepositorySettingsRequest(RecentRepositorySettingsResponse):
    pass


class InstructionFileSettingsResponse(StrictDto):
    root_max_lines: Annotated[int, Field(ge=1, le=10_000)]
    nested_max_lines: Annotated[int, Field(ge=1, le=10_000)]


class UpdateInstructionFileSettingsRequest(InstructionFileSettingsResponse):
    @model_validator(mode="after")
    def validate_scope_limits(self) -> "UpdateInstructionFileSettingsRequest":
        """Keep the root allowance at least as permissive as nested files."""

        if self.root_max_lines < self.nested_max_lines:
            raise ValueError("root_max_lines must be greater than or equal to nested_max_lines")
        return self


class ReviewCompletionSettingsResponse(StrictDto):
    max_incomplete_review_retries: Annotated[int, Field(ge=0, le=20)]


class UpdateReviewCompletionSettingsRequest(ReviewCompletionSettingsResponse):
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


class DeleteRecentRepositoryRequest(StrictDto):
    """Identify one recent repository shortcut by its canonical local path."""

    repository_path: Path


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


class RecentRepositoryResponse(StrictDto):
    """Return one selectable repository directory from recent Review history."""

    repository_path: str
    repository_name: str
    last_reviewed_at: datetime

    @classmethod
    def from_domain(cls, repository: RecentRepositoryRecord) -> "RecentRepositoryResponse":
        return cls(
            repository_path=str(repository.repository_path),
            repository_name=repository.repository_name,
            last_reviewed_at=repository.last_reviewed_at,
        )


class RepositoryCatalogRequest(StrictDto):
    """Request selectable refs for one validated exact repository root."""

    path: Path
    target_ref: RefLabel | None = None
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


class ToolUsageResponse(StrictDto):
    """Expose one tool's invocation and matched-result totals."""

    tool_name: str
    call_count: Annotated[int, Field(ge=0)]
    result_count: Annotated[int, Field(ge=0)]


class AgentProcessResponse(StrictDto):
    """Expose provider usage and tool activity for one Agent version."""

    agent: str
    model_name: str | None
    llm_call_count: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    tool_call_count: Annotated[int, Field(ge=0)]
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: Annotated[int, Field(ge=0)] | None


class ReviewProcessReportResponse(StrictDto):
    """Expose terminal Review metrics derived from its credential-safe transcript."""

    task_id: str
    status: str
    usage_is_complete: bool
    agent_run_count: Annotated[int, Field(ge=0)]
    llm_call_count: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    tool_call_count: Annotated[int, Field(ge=0)]
    tool_result_count: Annotated[int, Field(ge=0)]
    unmatched_tool_result_count: Annotated[int, Field(ge=0)]
    finding_count: Annotated[int, Field(ge=0)]
    transcript_entry_count: Annotated[int, Field(ge=0)]
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: Annotated[int, Field(ge=0)] | None
    tools: list[ToolUsageResponse]
    agents: list[AgentProcessResponse]

    @classmethod
    def from_application(cls, report: ReviewProcessReport) -> "ReviewProcessReportResponse":
        return cls.model_validate(asdict(report))


class CancelReviewRequest(StrictDto):
    pass


class ProblemResponse(StrictDto):
    code: str
    message: str


GatewayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
GatewayModel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
AgentTimeoutSeconds = Annotated[int, Field(ge=60, le=7200)]
MaxAgentTurns = Annotated[int, Field(ge=1, le=500)]
MaxToolCalls = Annotated[int, Field(ge=1, le=5000)]
MaxIdenticalToolResults = Annotated[int, Field(ge=2, le=20)]
ToolTimeoutSeconds = Annotated[int, Field(ge=1, le=300)]


class CreateModelGatewayRequest(StrictDto):
    """Validate one new named gateway while keeping its API key write-only."""

    name: GatewayName
    api_key: SecretStr
    model: GatewayModel
    base_url: AnyHttpUrl
    vendor: Literal["openai", "deepseek", "zhipu"] = "openai"
    api_type: Literal["responses", "chat_completions"] = "chat_completions"
    max_tokens: int = 65536
    thinking_level: Literal["disabled", "low", "medium", "high"] = "disabled"
    agent_timeout: AgentTimeoutSeconds = 1800
    max_agent_turns: MaxAgentTurns = 100
    max_tool_calls: MaxToolCalls = 300
    max_identical_tool_results: MaxIdenticalToolResults = 3
    tool_timeout_seconds: ToolTimeoutSeconds = 30

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
    vendor: Literal["openai", "deepseek", "zhipu"] = "openai"
    api_type: Literal["responses", "chat_completions"] = "chat_completions"
    max_tokens: int = 65536
    thinking_level: Literal["disabled", "low", "medium", "high"] = "disabled"
    agent_timeout: AgentTimeoutSeconds = 1800
    max_agent_turns: MaxAgentTurns = 100
    max_tool_calls: MaxToolCalls = 300
    max_identical_tool_results: MaxIdenticalToolResults = 3
    tool_timeout_seconds: ToolTimeoutSeconds = 30

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
    vendor: Literal["openai", "deepseek", "zhipu"]
    is_active: bool
    api_type: Literal["responses", "chat_completions"]
    max_tokens: int
    thinking_level: Literal["disabled", "low", "medium", "high"]
    agent_timeout: int
    max_agent_turns: int
    max_tool_calls: int
    max_identical_tool_results: int
    tool_timeout_seconds: int


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

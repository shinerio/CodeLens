from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

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

from codelens.findings.domain.existing_findings import ExistingFinding, ExistingFindingSet
from codelens.review.application.process_report import ReviewProcessReport
from codelens.review.domain.ports import RecentRepositoryRecord, ReviewRecord
from codelens.review.domain.review_profile import ReviewProfile
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
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


class AdaptiveReviewerSelectionDto(StrictDto):
    mode: Literal["adaptive"]


class FixedReviewerSelectionDto(StrictDto):
    mode: Literal["fixed"]
    reviewer_versions: Annotated[
        list[
            Annotated[
                str,
                StringConstraints(pattern=r"^[a-z][a-z0-9_.-]*:v2$"),
            ]
        ],
        Field(min_length=1),
    ]

    @model_validator(mode="after")
    def validate_reviewer_team(self) -> "FixedReviewerSelectionDto":
        """Reject duplicate, non-v2, and General team combinations."""

        if len(self.reviewer_versions) != len(set(self.reviewer_versions)):
            raise ValueError("reviewer_versions must be unique")
        if "general:v2" in self.reviewer_versions and self.reviewer_versions != ["general:v2"]:
            raise ValueError("general:v2 must run alone")
        return self


type ReviewerSelectionDto = Annotated[
    AdaptiveReviewerSelectionDto | FixedReviewerSelectionDto,
    Field(discriminator="mode"),
]


class CreateReviewProfileRequest(StrictDto):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    is_default: bool = False
    reviewer_selection: ReviewerSelectionDto


class UpdateReviewProfileRequest(CreateReviewProfileRequest):
    revision: Annotated[int, Field(ge=1)]


class CopyReviewProfileRequest(StrictDto):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ReviewProfileResponse(StrictDto):
    profile_id: str
    revision: int
    name: str
    is_default: bool
    reviewer_selection: ReviewerSelectionDto
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, profile: ReviewProfile) -> "ReviewProfileResponse":
        """Project a profile without exposing its persistence representation."""

        selection: AdaptiveReviewerSelectionDto | FixedReviewerSelectionDto
        if isinstance(profile.reviewer_selection, AdaptiveReviewerSelection):
            selection = AdaptiveReviewerSelectionDto(mode="adaptive")
        else:
            assert isinstance(profile.reviewer_selection, FixedReviewerSelection)
            selection = FixedReviewerSelectionDto(
                mode="fixed",
                reviewer_versions=list(profile.reviewer_selection.reviewer_versions),
            )
        return cls(
            profile_id=profile.profile_id,
            revision=profile.revision,
            name=profile.name,
            is_default=profile.is_default,
            reviewer_selection=selection,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


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


class RuntimeLoggingSettingsResponse(StrictDto):
    default_level: Literal["debug", "info", "warning", "error"]
    level: Literal["debug", "info", "warning", "error"]
    model_output_enabled: bool


class UpdateRuntimeLoggingSettingsRequest(StrictDto):
    level: Literal["debug", "info", "warning", "error"]
    model_output_enabled: bool


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


class TriggerIdempotencySettingsResponse(StrictDto):
    enabled: bool


class UpdateTriggerIdempotencySettingsRequest(TriggerIdempotencySettingsResponse):
    pass


class ToolLimitsResponse(StrictDto):
    """Expose configurable tool-level limits for Review Agent evidence operations."""

    max_results: Annotated[int, Field(ge=1, le=10_000)]
    max_read_bytes: Annotated[int, Field(ge=1024, le=10 * 1024 * 1024)]
    max_scan_bytes: Annotated[int, Field(ge=1024, le=100 * 1024 * 1024)]
    max_source_bytes: Annotated[int, Field(ge=1024, le=100 * 1024 * 1024)]
    max_file_payload_cache_bytes: Annotated[int, Field(ge=1, le=1024 * 1024 * 1024)]
    max_lines: Annotated[int, Field(ge=10, le=10_000)]
    max_path_chars: Annotated[int, Field(ge=64, le=4096)]
    max_pattern_chars: Annotated[int, Field(ge=64, le=4096)]
    regex_timeout_seconds: Annotated[float, Field(ge=1.0, le=300.0)]
    comment_batch_size: Annotated[int, Field(ge=1, le=100)]
    short_text_max: Annotated[int, Field(ge=64, le=2048)]
    long_text_max: Annotated[int, Field(ge=256, le=64_000)]
    task_summary_max: Annotated[int, Field(ge=256, le=64_000)]
    context_compaction_enabled: bool
    context_compaction_trigger_tokens: Annotated[int, Field(ge=512, le=500000)]
    context_compaction_keep_recent_evidence_results: Annotated[int, Field(ge=0, le=100)]
    context_compaction_max_retries: Annotated[int, Field(ge=0, le=10)]
    context_compaction_retry_backoff_base: Annotated[float, Field(ge=0.1, le=60.0)]
    context_compaction_retry_max_delay: Annotated[float, Field(ge=1.0, le=300.0)]
    context_compaction_max_consecutive_failures: Annotated[int, Field(ge=1, le=10)]


class UpdateToolLimitsRequest(StrictDto):
    """Accept partial updates for tool limits; omitted fields retain current values."""

    max_results: Annotated[int | None, Field(ge=1, le=10_000)] = None
    max_read_bytes: Annotated[int | None, Field(ge=1024, le=10 * 1024 * 1024)] = None
    max_scan_bytes: Annotated[int | None, Field(ge=1024, le=100 * 1024 * 1024)] = None
    max_source_bytes: Annotated[int | None, Field(ge=1024, le=100 * 1024 * 1024)] = None
    max_file_payload_cache_bytes: Annotated[
        int | None, Field(ge=1, le=1024 * 1024 * 1024)
    ] = None
    max_lines: Annotated[int | None, Field(ge=10, le=10_000)] = None
    max_path_chars: Annotated[int | None, Field(ge=64, le=4096)] = None
    max_pattern_chars: Annotated[int | None, Field(ge=64, le=4096)] = None
    regex_timeout_seconds: Annotated[float | None, Field(ge=1.0, le=300.0)] = None
    comment_batch_size: Annotated[int | None, Field(ge=1, le=100)] = None
    short_text_max: Annotated[int | None, Field(ge=64, le=2048)] = None
    long_text_max: Annotated[int | None, Field(ge=256, le=64_000)] = None
    task_summary_max: Annotated[int | None, Field(ge=256, le=64_000)] = None
    context_compaction_enabled: bool | None = None
    context_compaction_trigger_tokens: Annotated[
        int | None, Field(ge=512, le=500000)
    ] = None
    context_compaction_keep_recent_evidence_results: Annotated[int | None, Field(ge=0, le=100)] = (
        None
    )
    context_compaction_max_retries: Annotated[int | None, Field(ge=0, le=10)] = None
    context_compaction_retry_backoff_base: Annotated[
        float | None, Field(ge=0.1, le=60.0)
    ] = None
    context_compaction_retry_max_delay: Annotated[
        float | None, Field(ge=1.0, le=300.0)
    ] = None
    context_compaction_max_consecutive_failures: Annotated[
        int | None, Field(ge=1, le=10)
    ] = None


class NodeSettingsResponse(StrictDto):
    """Expose process-level resource limits (applied on next restart)."""

    memory_limit_mb: Annotated[int, Field(ge=512, le=1024 * 1024)]
    memory_check_interval_seconds: Annotated[float, Field(ge=0.01, le=3600.0)]
    memory_cleanup_threshold_ratio: Annotated[float, Field(gt=0.0, le=1.0)]
    memory_reject_threshold_ratio: Annotated[float, Field(gt=0.0, le=1.0)]
    max_active_reviews: Annotated[int, Field(ge=1, le=100)]
    max_active_agent_runs: Annotated[int, Field(ge=1, le=200)]
    max_agent_runs_per_review: Annotated[int, Field(ge=1, le=200)]


class UpdateNodeSettingsRequest(StrictDto):
    """Accept partial updates for node settings; omitted fields retain current values."""

    memory_limit_mb: Annotated[int | None, Field(ge=512, le=1024 * 1024)] = None
    memory_check_interval_seconds: Annotated[float | None, Field(ge=0.01, le=3600.0)] = None
    memory_cleanup_threshold_ratio: Annotated[float | None, Field(gt=0.0, le=1.0)] = None
    memory_reject_threshold_ratio: Annotated[float | None, Field(gt=0.0, le=1.0)] = None
    max_active_reviews: Annotated[int | None, Field(ge=1, le=100)] = None
    max_active_agent_runs: Annotated[int | None, Field(ge=1, le=200)] = None
    max_agent_runs_per_review: Annotated[int | None, Field(ge=1, le=200)] = None


class FileExclusionSettingsResponse(StrictDto):
    """Expose the Web-managed overlay for subsequent Review tasks."""

    suffixes: Annotated[list[str], Field(max_length=128)]
    path_regexes: Annotated[list[str], Field(max_length=128)]


class UpdateFileExclusionSettingsRequest(StrictDto):
    """Accept an atomic partial replacement of the Web-managed overlay."""

    suffixes: Annotated[list[str] | None, Field(max_length=128)] = None
    path_regexes: Annotated[list[str] | None, Field(max_length=128)] = None


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
    """Expose branch options, the selected branch tip, and a paginated commit summary page."""

    branches: list[RepositoryBranchResponse]
    commits: list[RepositoryCommitResponse]
    next_commit_offset: int | None
    target_commit: RepositoryCommitResponse | None = None


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


class ExistingFindingRequest(StrictDto):
    """Accept one bounded historical issue for duplicate suppression context."""

    source_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    finding_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    content: Annotated[str, StringConstraints(min_length=1, max_length=8_000)]
    path: str | None = None
    side: Literal["old", "new"] | None = None
    start_line: Annotated[int, Field(ge=1)] | None = None
    end_line: Annotated[int, Field(ge=1)] | None = None
    existing_code: Annotated[str, StringConstraints(min_length=1, max_length=8_000)] | None = None
    fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    recommendation: Annotated[str, StringConstraints(min_length=1, max_length=8_000)] | None = None
    category: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    severity: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None

    def to_domain(self) -> ExistingFinding:
        return ExistingFinding(**self.model_dump())

    @model_validator(mode="after")
    def validate_domain_invariants(self) -> "ExistingFindingRequest":
        """Surface unsafe paths and incomplete locations as HTTP validation errors."""

        self.to_domain()
        return self


class CreateReviewRequest(StrictDto):
    repository_path: Path
    scope: ScopeRequest
    reviewer_selection: ReviewerSelectionDto
    profile_source: "ReviewProfileSourceDto | None" = None
    prompt_locale: Literal["en", "zh-CN"] = "en"
    external_context: dict[str, Any] | None = None
    existing_findings: Annotated[list[ExistingFindingRequest], Field(max_length=500)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_existing_findings_budget(self) -> "CreateReviewRequest":
        """Reject an oversized canonical set before entering the application layer."""

        ExistingFindingSet.from_findings(
            tuple(finding.to_domain() for finding in self.existing_findings)
        )
        return self

    def review_profile_snapshot(self) -> ReviewProfileSnapshot:
        selection = self.reviewer_selection
        domain_selection = (
            AdaptiveReviewerSelection()
            if isinstance(selection, AdaptiveReviewerSelectionDto)
            else FixedReviewerSelection(tuple(selection.reviewer_versions))
        )
        source = self.profile_source
        return ReviewProfileSnapshot(
            reviewer_selection=domain_selection,
            source_profile_id=source.profile_id if source is not None else None,
            source_profile_revision=source.revision if source is not None else None,
        )


class ReviewProfileSourceDto(StrictDto):
    profile_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    revision: Annotated[int, Field(ge=1)]


class UpdateAgentPromptRequest(StrictDto):
    prompt: Annotated[str, Field(min_length=1, max_length=100_000)]


class AgentPromptResponse(StrictDto):
    agent_id: str
    version: int
    locale: Literal["en", "zh-CN"]
    system_prompt: str
    prompt: str
    is_custom: bool


class AgentPromptCatalogEntryResponse(StrictDto):
    reference: str
    agent_id: str
    version: int
    role: Literal["planner", "reviewer", "verifier", "deduplicator", "remediator"]
    dimensions: list[str]
    capability_readiness: Literal["ready"]


class ReviewResponse(StrictDto):
    task_id: str
    status: str
    scope_type: str
    base_oid: str
    head_oid: str
    base_ref: str | None = None
    target_ref: str | None = None
    selected_agents: list[str]
    worktree_status: Literal["pending"] = "pending"
    repository_id: str
    repository_realpath_hash: str
    git_common_dir_hash: str
    cancellation_requested: bool
    repository_name: str
    created_at: datetime
    finding_count: Annotated[int, Field(ge=0)] = 0
    external_context: dict[str, Any] | None = None
    selection_request: dict[str, object]
    profile_source: dict[str, object] | None = None
    review_plan: dict[str, object] | None = None
    coverage: dict[str, list[str]]
    verdict_summary: dict[str, int]

    @classmethod
    def from_domain(
        cls,
        review: ReviewRecord,
        *,
        selected_agents: list[str] | None = None,
        review_plan: dict[str, object] | None = None,
        coverage: dict[str, list[str]] | None = None,
        verdict_summary: dict[str, int] | None = None,
    ) -> "ReviewResponse":
        selection = review.review_profile.reviewer_selection
        selection_request: dict[str, object] = (
            {"mode": "adaptive"}
            if isinstance(selection, AdaptiveReviewerSelection)
            else {
                "mode": "fixed",
                "reviewer_versions": list(selection.reviewer_versions),
            }
        )
        source: dict[str, object] | None = (
            {
                "profile_id": review.review_profile.source_profile_id,
                "revision": review.review_profile.source_profile_revision,
            }
            if review.review_profile.source_profile_id is not None
            else None
        )
        return cls(
            task_id=review.task_id,
            status=review.status,
            scope_type=review.scope_type,
            base_oid=review.base_oid,
            head_oid=review.head_oid,
            base_ref=review.base_ref,
            target_ref=review.target_ref,
            selected_agents=selected_agents or list(review.selected_agent_versions),
            repository_id=review.repository_id,
            repository_realpath_hash=review.repository_realpath_hash,
            git_common_dir_hash=review.git_common_dir_hash,
            cancellation_requested=review.cancellation_requested,
            repository_name=review.repository_name,
            created_at=review.created_at,
            finding_count=review.finding_count,
            external_context=review.external_context,
            selection_request=selection_request,
            profile_source=source,
            review_plan=review_plan,
            coverage=coverage or {"planned": [], "completed": [], "failed": [], "omitted": []},
            verdict_summary=verdict_summary or {"accept": 0, "deny": 0, "merge": 0},
        )


class ToolUsageResponse(StrictDto):
    """Expose one tool's invocation and matched-result totals."""

    tool_name: str
    call_count: Annotated[int, Field(ge=0)]
    result_count: Annotated[int, Field(ge=0)]
    accepted_call_count: Annotated[int, Field(ge=0)]
    rejected_call_count: Annotated[int, Field(ge=0)]
    unclassified_call_count: Annotated[int, Field(ge=0)]


class RejectedToolCallResponse(StrictDto):
    """Expose one safe rejected invocation reason without tool arguments or result text."""

    agent: str
    tool_name: str
    tool_call_id: str | None
    reason_code: str
    reason: str


class InvalidToolUsageResponse(StrictDto):
    """Expose a provider-issued tool name rejected before dispatch."""

    tool_name: str
    call_count: Annotated[int, Field(ge=0)]


class AgentProcessResponse(StrictDto):
    """Expose provider usage and tool activity for one Agent version."""

    agent: str
    model_name: str | None
    llm_call_count: Annotated[int, Field(ge=0)]
    checkpoint_llm_call_count: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    checkpoint_input_tokens: Annotated[int, Field(ge=0)]
    cached_input_tokens: Annotated[int, Field(ge=0)]
    context_compaction_count: Annotated[int, Field(ge=0)]
    context_compacted_result_count: Annotated[int, Field(ge=0)]
    context_compaction_original_tokens: Annotated[int, Field(ge=0)]
    context_compaction_compressed_tokens: Annotated[int, Field(ge=0)]
    context_compaction_failure_count: Annotated[int, Field(ge=0)]
    compaction_replay_registered_count: Annotated[int, Field(ge=0)]
    compaction_replay_consumed_count: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    checkpoint_output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    tool_call_count: Annotated[int, Field(ge=0)]
    accepted_tool_call_count: Annotated[int, Field(ge=0)]
    rejected_tool_call_count: Annotated[int, Field(ge=0)]
    unclassified_tool_call_count: Annotated[int, Field(ge=0)]
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: Annotated[int, Field(ge=0)] | None


class ReviewProcessReportResponse(StrictDto):
    """Expose live Review metrics derived from its credential-safe transcript."""

    task_id: str
    status: str
    usage_is_complete: bool
    agent_run_count: Annotated[int, Field(ge=0)]
    llm_call_count: Annotated[int, Field(ge=0)]
    checkpoint_llm_call_count: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    checkpoint_input_tokens: Annotated[int, Field(ge=0)]
    cached_input_tokens: Annotated[int, Field(ge=0)]
    context_compaction_count: Annotated[int, Field(ge=0)]
    context_compacted_result_count: Annotated[int, Field(ge=0)]
    context_compaction_original_tokens: Annotated[int, Field(ge=0)]
    context_compaction_compressed_tokens: Annotated[int, Field(ge=0)]
    context_compaction_failure_count: Annotated[int, Field(ge=0)]
    compaction_replay_registered_count: Annotated[int, Field(ge=0)]
    compaction_replay_consumed_count: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    checkpoint_output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    tool_call_count: Annotated[int, Field(ge=0)]
    accepted_tool_call_count: Annotated[int, Field(ge=0)]
    rejected_tool_call_count: Annotated[int, Field(ge=0)]
    unclassified_tool_call_count: Annotated[int, Field(ge=0)]
    invalid_tool_call_count: Annotated[int, Field(ge=0)]
    tool_result_count: Annotated[int, Field(ge=0)]
    unmatched_tool_result_count: Annotated[int, Field(ge=0)]
    non_json_tool_result_count: Annotated[int, Field(ge=0)]
    loop_abort_count: Annotated[int, Field(ge=0)]
    tool_result_status_counts: dict[str, Annotated[int, Field(ge=0)]]
    finding_count: Annotated[int, Field(ge=0)]
    transcript_entry_count: Annotated[int, Field(ge=0)]
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: Annotated[int, Field(ge=0)] | None
    tools: list[ToolUsageResponse]
    invalid_tools: list[InvalidToolUsageResponse]
    rejected_tool_calls: list[RejectedToolCallResponse]
    agents: list[AgentProcessResponse]

    @classmethod
    def from_application(cls, report: ReviewProcessReport) -> "ReviewProcessReportResponse":
        return cls.model_validate(asdict(report))


class CancelReviewRequest(StrictDto):
    pass


class RetryReviewRequest(StrictDto):
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
MaxRetries = Annotated[int, Field(ge=0, le=10)]
RetryBackoffBase = Annotated[float, Field(ge=0.1, le=60.0)]
RetryMaxDelay = Annotated[float, Field(ge=1.0, le=300.0)]
NoProgressRoundsThreshold = Annotated[int, Field(ge=1, le=100)]


class CreateModelGatewayRequest(StrictDto):
    """Validate one new named gateway while keeping its API key write-only."""

    name: GatewayName
    api_key: SecretStr
    model: GatewayModel
    base_url: AnyHttpUrl
    vendor: Literal["openai", "deepseek", "zhipu", "qwen"] = "openai"
    api_type: Literal["responses", "chat_completions"] = "chat_completions"
    max_tokens: int = 65536
    thinking_level: Literal["disabled", "low", "medium", "high"] = "disabled"
    agent_timeout: AgentTimeoutSeconds = 3600
    max_agent_turns: MaxAgentTurns = 500
    max_tool_calls: MaxToolCalls = 500
    max_identical_tool_results: MaxIdenticalToolResults = 3
    tool_timeout_seconds: ToolTimeoutSeconds = 30
    max_retries: MaxRetries = 10
    retry_backoff_base: RetryBackoffBase = 1.0
    retry_max_delay: RetryMaxDelay = 30.0
    no_progress_rounds_threshold: NoProgressRoundsThreshold = 10

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
    vendor: Literal["openai", "deepseek", "zhipu", "qwen"] = "openai"
    api_type: Literal["responses", "chat_completions"] = "chat_completions"
    max_tokens: int = 65536
    thinking_level: Literal["disabled", "low", "medium", "high"] = "disabled"
    agent_timeout: AgentTimeoutSeconds = 3600
    max_agent_turns: MaxAgentTurns = 500
    max_tool_calls: MaxToolCalls = 500
    max_identical_tool_results: MaxIdenticalToolResults = 3
    tool_timeout_seconds: ToolTimeoutSeconds = 30
    max_retries: MaxRetries = 10
    retry_backoff_base: RetryBackoffBase = 1.0
    retry_max_delay: RetryMaxDelay = 30.0
    no_progress_rounds_threshold: NoProgressRoundsThreshold = 10

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
    vendor: Literal["openai", "deepseek", "zhipu", "qwen"]
    is_active: bool
    api_type: Literal["responses", "chat_completions"]
    max_tokens: int
    thinking_level: Literal["disabled", "low", "medium", "high"]
    agent_timeout: int
    max_agent_turns: int
    max_tool_calls: int
    max_identical_tool_results: int
    tool_timeout_seconds: int
    max_retries: int
    retry_backoff_base: float
    retry_max_delay: float
    no_progress_rounds_threshold: int


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


class ResetAllSettingsResponse(StrictDto):
    """Aggregate response after resetting all user-facing settings to defaults."""

    instruction_files: InstructionFileSettingsResponse
    file_exclusions: FileExclusionSettingsResponse
    review_completion: ReviewCompletionSettingsResponse
    trigger_idempotency: TriggerIdempotencySettingsResponse
    recent_repositories: RecentRepositorySettingsResponse
    tool_limits: ToolLimitsResponse
    node_settings: NodeSettingsResponse
    logging: RuntimeLoggingSettingsResponse
    model_gateways: ModelGatewayCatalogResponse

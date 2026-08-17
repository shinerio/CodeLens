import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from codelens.capabilities.domain.models import FrozenAgentExecutionSpec
from codelens.findings.domain.candidates import CandidateFinding, CandidateFindingBatch
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.verdict import VerdictDecision
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.review_plan import ReviewPlan
from codelens.review.domain.review_profile import ReviewProfile
from codelens.review.domain.review_strategy import (
    FixedReviewerSelection,
    ReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.review.domain.tool_limits import ToolLimits
from codelens.workspace.domain.models import ReviewScopeType, ReviewSnapshot

DEFAULT_RECENT_REPOSITORY_LIMIT = 10
MIN_RECENT_REPOSITORY_LIMIT = 1
MAX_RECENT_REPOSITORY_LIMIT = 20

type AgentReviewCompletionStatus = Literal["complete", "incomplete"]


class ReviewProfileRepository(Protocol):
    """Persist profiles while keeping exactly one default in each transaction."""

    async def list_review_profiles(self) -> tuple[ReviewProfile, ...]: ...

    async def create_review_profile(self, profile: ReviewProfile) -> ReviewProfile: ...

    async def update_review_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        name: str,
        is_default: bool,
        reviewer_selection: ReviewerSelection,
        updated_at: datetime,
    ) -> ReviewProfile: ...

    async def copy_review_profile(
        self,
        profile_id: str,
        *,
        new_profile_id: str,
        name: str,
        created_at: datetime,
    ) -> ReviewProfile: ...

    async def delete_review_profile(self, profile_id: str) -> None: ...

    async def set_default_review_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        updated_at: datetime,
    ) -> ReviewProfile: ...


@dataclass(frozen=True)
class SnapshotRead:
    """Return bounded bytes plus their full immutable content identity."""

    content: bytes
    content_hash: str
    truncated: bool


class SnapshotFileReaderPort(Protocol):
    """Read bounded line ranges only from a verified task-owned Snapshot."""

    async def read(
        self,
        snapshot: ReviewSnapshot,
        path: str,
        start_line: int,
        end_line: int,
        side: str,
        max_bytes: int,
    ) -> SnapshotRead:
        raise NotImplementedError


@dataclass(frozen=True)
class AgentResponseDiagnostic:
    """Retain bounded public response metadata without provider payload bodies."""

    response_id: str | None
    request_id: str | None
    input_tokens: int
    output_tokens: int
    output_item_count: int
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    phase: Literal["agent", "checkpoint_compaction"] = "agent"


@dataclass(frozen=True)
class UnvalidatedAgentOutput:
    """Carry canonical model output and bounded diagnostics before validation."""

    canonical_bytes: bytes
    response_ids: tuple[str, ...]
    model_name: str
    input_tokens: int
    output_tokens: int
    diagnostics: tuple[AgentResponseDiagnostic, ...]
    incomplete_review_files: tuple[str, ...] = ()
    context_compaction_count: int = 0
    context_compacted_result_count: int = 0
    context_compaction_original_tokens: int = 0
    context_compaction_compressed_tokens: int = 0
    context_compaction_failure_count: int = 0
    compaction_replay_registered_count: int = 0
    compaction_replay_consumed_count: int = 0

    @property
    def review_completion_status(self) -> AgentReviewCompletionStatus:
        """Classify an accepted Agent completion without duplicating its coverage evidence."""

        return "incomplete" if self.incomplete_review_files else "complete"

    @property
    def cached_input_tokens(self) -> int:
        """Return provider-reported cache-hit input tokens across model requests."""

        return sum(item.cached_input_tokens for item in self.diagnostics)

    @property
    def cache_write_input_tokens(self) -> int:
        """Return provider-reported cache-write input tokens across model requests."""

        return sum(item.cache_write_input_tokens for item in self.diagnostics)

    @property
    def checkpoint_llm_call_count(self) -> int:
        """Return the number of independent semantic checkpoint model calls."""

        return sum(item.phase == "checkpoint_compaction" for item in self.diagnostics)

    @property
    def checkpoint_input_tokens(self) -> int:
        """Return input tokens spent on semantic checkpoint calls."""

        return sum(
            item.input_tokens
            for item in self.diagnostics
            if item.phase == "checkpoint_compaction"
        )

    @property
    def checkpoint_output_tokens(self) -> int:
        """Return output tokens spent on semantic checkpoint calls."""

        return sum(
            item.output_tokens
            for item in self.diagnostics
            if item.phase == "checkpoint_compaction"
        )


@dataclass(frozen=True)
class AgentRuntimeEvent:
    """One complete observable model or tool event emitted while an Agent is running."""

    kind: Literal[
        "prompt",
        "model_started",
        "model_reasoning_delta",
        "model_reasoning_completed",
        "model_output_delta",
        "model_output_completed",
        "model_completed",
        "model_raw_output",
        "checkpoint_compaction",
        "tool_call",
        "invalid_tool_call",
        "invalid_tool_result",
        "tool_result",
        "skill_loaded",
        "lifecycle",
    ]
    content: str
    metadata: dict[str, str]


type AgentRuntimeEventSink = Callable[[AgentRuntimeEvent], Awaitable[None]]


@dataclass(frozen=True)
class RunOutputArtifact:
    """Identify a persisted unvalidated output without exposing its storage path."""

    reference: str
    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class ReviewRecord:
    """Expose durable review state without leaking persistence rows or filesystem paths."""

    task_id: str
    repository_id: str
    repository_realpath_hash: str
    git_common_dir_hash: str
    scope_type: str
    base_oid: str
    head_oid: str
    base_ref: str | None
    target_ref: str | None
    selected_agent_versions: tuple[str, ...]
    review_profile: ReviewProfileSnapshot
    planning_context_json: str | None
    planning_context_hash: str | None
    trigger_source: str | None
    supersede_policy: str | None
    status: str
    cancellation_requested: bool
    repository_name: str
    created_at: datetime
    is_deleted: bool
    finding_count: int = 0
    external_context: dict[str, Any] | None = None
    has_partial_coverage: bool = False
    existing_finding_count: int = 0


@dataclass(frozen=True)
class RecentRepositoryRecord:
    """Expose one repository directory from the bounded recent-use catalog."""

    repository_path: Path
    repository_name: str
    last_reviewed_at: datetime


@dataclass(frozen=True)
class ReviewExecutionRecord:
    """Carry the private durable inputs needed to reconstruct one Worker execution."""

    task_id: str
    repository_path: Path
    repository_realpath_hash: str
    git_common_dir_hash: str
    base_oid: str
    head_oid: str
    scope_type: ReviewScopeType
    base_ref: str | None
    target_ref: str | None
    overlay_hash: str | None
    overlay_artifact_ref: str | None
    candidate_paths: tuple[str, ...]
    file_exclusion_policy_json: str
    file_exclusion_policy_hash: str
    selected_agent_versions: tuple[str, ...]
    prompt_locale: str
    status: str
    cancellation_requested: bool
    review_profile: ReviewProfileSnapshot = field(
        default_factory=lambda: ReviewProfileSnapshot(FixedReviewerSelection(("correctness:v2",)))
    )
    planning_context_json: str | None = None
    planning_context_hash: str | None = None
    has_partial_coverage: bool = False
    existing_findings_json: str = "[]"
    existing_findings_hash: str = hashlib.sha256(b"[]").hexdigest()


@dataclass(frozen=True)
class ReviewEvent:
    """Expose one ordered, redacted outbox event for resumable delivery."""

    event_id: int
    task_id: str
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArtifactIdentity:
    """Reference one hash-verified restricted Artifact without exposing its bytes."""

    reference: str
    content_hash: str


@dataclass(frozen=True)
class ReviewPlanRecord:
    """Carry one hash-verified frozen plan and its selection-policy identities."""

    plan: ReviewPlan
    catalog_version: str
    capability_fingerprint: str
    created_at: datetime


@dataclass(frozen=True)
class AgentExecutionSpecRecord:
    """Expose safe frozen execution metadata plus restricted Artifact identities."""

    task_id: str
    logical_node_id: str
    spec_json: str
    fingerprint: str
    prompt_artifact_ref: str
    prompt_artifact_hash: str
    skill_artifacts: tuple[ArtifactIdentity, ...]
    created_at: datetime


class ReviewPlanStorePort(Protocol):
    """Create and reload one immutable Review Plan per task."""

    async def save(
        self,
        plan: ReviewPlan,
        *,
        catalog_version: str,
        capability_fingerprint: str,
    ) -> ReviewPlanRecord: ...

    async def get(self, task_id: str) -> ReviewPlanRecord | None: ...


class AgentExecutionSpecStorePort(Protocol):
    """Persist safe spec metadata while keeping prompt and Skill bodies in Artifacts."""

    async def save(
        self,
        *,
        task_id: str,
        logical_node_id: str,
        execution_spec: FrozenAgentExecutionSpec,
        prompt_artifact_ref: str,
        prompt_artifact_hash: str,
        skill_artifacts: tuple[ArtifactIdentity, ...],
    ) -> AgentExecutionSpecRecord: ...

    async def get(self, task_id: str, logical_node_id: str) -> AgentExecutionSpecRecord | None: ...

    async def list_for_task(self, task_id: str) -> tuple[AgentExecutionSpecRecord, ...]: ...


class CandidateFindingStorePort(Protocol):
    """Persist validated pre-publication Candidate audit records."""

    async def list_for_task(self, task_id: str) -> tuple[CandidateFinding, ...]: ...


class VerdictStorePort(Protocol):
    """Persist deterministic clusters and Final Verifier decisions."""

    async def save_clusters(
        self, task_id: str, snapshot_id: str, clusters: tuple[FindingCluster, ...]
    ) -> None: ...

    async def list_clusters(self, task_id: str) -> tuple[FindingCluster, ...]: ...

    async def save_decisions(
        self, task_id: str, decisions: tuple[VerdictDecision, ...]
    ) -> None: ...

    async def list_decisions(self, task_id: str) -> tuple[VerdictDecision, ...]: ...


class RecentRepositoryStorePort(Protocol):
    """Manage the repository-use catalog independently from Review visibility."""

    async def get_limit(self) -> int:
        """Return the persisted LRU capacity."""

        raise NotImplementedError

    async def update_limit(self, limit: int) -> int:
        """Persist a validated capacity and immediately prune overflow."""

        raise NotImplementedError

    async def list_recent_repositories(
        self,
        limit: int,
    ) -> tuple[RecentRepositoryRecord, ...]:
        """Return repository directories from most to least recently used."""

        raise NotImplementedError

    async def delete_recent_repository(self, repository_path: Path) -> None:
        """Idempotently remove one repository directory from the recent-use catalog."""

        raise NotImplementedError


class ReviewStorePort(Protocol):
    """Persist review commands and expose path-free task summaries."""

    async def create_with_job(self, task: ReviewTask) -> None:
        """Atomically persist a task, singleton job, and creation event."""

        raise NotImplementedError

    async def get_review(self, task_id: str) -> ReviewRecord | None:
        """Return one review summary when it exists."""

        raise NotImplementedError

    async def find_duplicate_review(
        self,
        repository_id: str,
        base_oid: str,
        head_oid: str,
    ) -> ReviewRecord | None:
        """Return the newest non-deleted, non-failed review matching the commit range."""

        raise NotImplementedError

    async def create_triggered_with_job(self, task: ReviewTask) -> tuple[ReviewRecord, bool]:
        """Atomically deduplicate, supersede/cancel older slot tasks, and enqueue one task."""

        raise NotImplementedError

    async def list_reviews(self) -> tuple[ReviewRecord, ...]:
        """Return every visible review workspace in newest-first order."""

        raise NotImplementedError

    async def retry_failed_review(
        self,
        source_task_id: str,
        new_task_id: str,
        created_at: datetime,
    ) -> ReviewRecord | None:
        """Create an independent queued task from one visible failed Review."""

        raise NotImplementedError

    async def soft_delete_review(self, task_id: str) -> bool:
        """Hide one review and request cancellation when it is still active."""

        raise NotImplementedError

    async def request_cancellation(self, task_id: str) -> ReviewRecord | None:
        """Atomically set cancellation intent and append its event once."""

        raise NotImplementedError


class ReviewEventPort(Protocol):
    """Read ordered durable events without exposing their storage adapter."""

    async def list_after(self, task_id: str, *, after_event_id: int) -> tuple[ReviewEvent, ...]:
        """Return events strictly after one validated event ID."""

        raise NotImplementedError


class AgentRuntimePort(Protocol):
    """Invoke one immutable Agent node through a provider-neutral boundary."""

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        """Return canonical untrusted output plus redacted usage diagnostics."""

        raise NotImplementedError


class RunArtifactPort(Protocol):
    """Persist and hash-verify unvalidated Agent output before schema validation."""

    async def write_output(self, run_id: str, payload: bytes) -> RunOutputArtifact:
        """Atomically persist canonical unvalidated bytes."""

        raise NotImplementedError

    async def read_output(self, reference: str, expected_hash: str) -> bytes:
        """Load output bytes only when the opaque reference and hash are valid."""

        raise NotImplementedError


@dataclass(frozen=True)
class FindingValidationWarning:
    """Identify one rejected candidate using bounded, user-safe diagnostics."""

    candidate_index: int
    reason_code: Literal["duplicate", "invalid"]
    message: str


class AgentRunCompletionPort(Protocol):
    """Atomically persist trusted Findings, node success, and an outbox event."""

    async def complete_with_candidates(
        self,
        task_id: str,
        node_key: str,
        candidates: CandidateFindingBatch,
        *,
        result_summary: dict[str, Any] | None = None,
    ) -> None:
        """Atomically persist Candidates, node success, and one completion event."""

        raise NotImplementedError

    async def complete_with_verdicts(
        self,
        task_id: str,
        node_key: str,
        decisions: tuple[VerdictDecision, ...],
    ) -> None:
        """Atomically persist Final Verifier decisions and complete their AgentRun."""

        raise NotImplementedError


class ToolLimitsStorePort(Protocol):
    """Persist and provide configurable tool-level limits for Agent evidence operations."""

    def get_tool_limits(self) -> ToolLimits:
        """Load persisted tool limits or product defaults."""

        raise NotImplementedError

    def save_tool_limits(self, limits: ToolLimits) -> None:
        """Atomically replace the persisted tool limits."""

        raise NotImplementedError

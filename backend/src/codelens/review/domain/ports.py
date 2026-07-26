from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from codelens.findings.domain.models import FindingBatch
from codelens.review.domain.models import ReviewTask
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.workspace.domain.models import ReviewScopeType, ReviewSnapshot

DEFAULT_RECENT_REPOSITORY_LIMIT = 10
MIN_RECENT_REPOSITORY_LIMIT = 1
MAX_RECENT_REPOSITORY_LIMIT = 20


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


@dataclass(frozen=True)
class UnvalidatedAgentOutput:
    """Carry canonical model output and bounded diagnostics before validation."""

    canonical_bytes: bytes
    response_ids: tuple[str, ...]
    model_name: str
    input_tokens: int
    output_tokens: int
    diagnostics: tuple[AgentResponseDiagnostic, ...]


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
        "tool_call",
        "tool_result",
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
    selected_agent_versions: tuple[str, ...]
    status: str
    cancellation_requested: bool
    repository_name: str
    created_at: datetime
    is_deleted: bool


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
    overlay_hash: str | None
    overlay_artifact_ref: str | None
    target_paths: tuple[str, ...]
    selected_agent_versions: tuple[str, ...]
    prompt_locale: str
    status: str
    cancellation_requested: bool


@dataclass(frozen=True)
class ReviewEvent:
    """Expose one ordered, redacted outbox event for resumable delivery."""

    event_id: int
    task_id: str
    event_type: str
    payload: dict[str, object]


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

    async def list_reviews(self) -> tuple[ReviewRecord, ...]:
        """Return every visible review workspace in newest-first order."""

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
        agent: AgentVersion,
        input_payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        """Return canonical untrusted output plus redacted usage diagnostics."""

        raise NotImplementedError


class AgentOutputCodecPort(Protocol):
    """Expose a versioned cross-context model output contract to a runtime adapter."""

    @property
    def schema_version(self) -> str:
        """Return the immutable output contract version accepted by this codec."""

        raise NotImplementedError

    def encode(self, final_output: object) -> bytes:
        """Revalidate untrusted output and return canonical checkpoint bytes."""

        raise NotImplementedError


class RunArtifactPort(Protocol):
    """Persist and hash-verify unvalidated Agent output before schema validation."""

    async def write_output(self, run_id: str, payload: bytes) -> RunOutputArtifact:
        """Atomically persist canonical unvalidated bytes."""

        raise NotImplementedError

    async def read_output(self, reference: str, expected_hash: str) -> bytes:
        """Load output bytes only when the opaque reference and hash are valid."""

        raise NotImplementedError


class FindingBatchValidationPort(Protocol):
    """Convert untrusted canonical output into trusted domain Findings."""

    @property
    def warnings(self) -> tuple["FindingValidationWarning", ...]:
        """Describe candidate-level rejections without exposing model content."""

        raise NotImplementedError

    async def validate(self, payload: bytes) -> FindingBatch:
        """Apply schema, path, line, hunk, and evidence validation."""

        raise NotImplementedError


@dataclass(frozen=True)
class FindingValidationWarning:
    """Identify one rejected candidate using bounded, user-safe diagnostics."""

    candidate_index: int
    reason_code: Literal["duplicate", "invalid"]
    message: str


class AgentRunCompletionPort(Protocol):
    """Atomically persist trusted Findings, node success, and an outbox event."""

    async def complete_with_findings(
        self,
        task_id: str,
        node_key: str,
        findings: FindingBatch,
    ) -> None:
        """Complete only an OUTPUT_SAVED or VALIDATING run in one transaction."""

        raise NotImplementedError

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from codelens.findings.domain.models import FindingBatch
from codelens.review.domain.models import ReviewTask
from codelens.reviewer_catalog.domain.models import AgentVersion


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
class ReviewExecutionRecord:
    """Carry the private durable inputs needed to reconstruct one Worker execution."""

    task_id: str
    repository_path: Path
    repository_realpath_hash: str
    git_common_dir_hash: str
    base_oid: str
    head_oid: str
    overlay_hash: str | None
    overlay_artifact_ref: str | None
    target_paths: tuple[str, ...]
    selected_agent_versions: tuple[str, ...]
    status: str
    cancellation_requested: bool


@dataclass(frozen=True)
class ReviewEvent:
    """Expose one ordered, redacted outbox event for resumable delivery."""

    event_id: int
    task_id: str
    event_type: str
    payload: dict[str, object]


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

    async def invoke(self, agent: AgentVersion, input_payload: bytes) -> UnvalidatedAgentOutput:
        """Return canonical untrusted output plus redacted usage diagnostics."""

        raise NotImplementedError


class AgentOutputCodecPort(Protocol):
    """Expose a versioned cross-context model output contract to a runtime adapter."""

    @property
    def schema_version(self) -> str:
        """Return the immutable output contract version accepted by this codec."""

        raise NotImplementedError

    @property
    def output_type(self) -> type[object]:
        """Return the boundary model type passed to the structured-output SDK."""

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

    async def validate(self, payload: bytes) -> FindingBatch:
        """Apply schema, path, line, hunk, and evidence validation."""

        raise NotImplementedError


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

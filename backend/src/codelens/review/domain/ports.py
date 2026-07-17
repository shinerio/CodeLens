from dataclasses import dataclass
from typing import Protocol

from codelens.findings.domain.models import FindingBatch
from codelens.reviewer_catalog.domain.models import AgentVersion


@dataclass(frozen=True)
class UnvalidatedAgentOutput:
    """Carry canonical model output and bounded diagnostics before validation."""

    canonical_bytes: bytes
    response_ids: tuple[str, ...]
    model_name: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RunOutputArtifact:
    """Identify a persisted unvalidated output without exposing its storage path."""

    reference: str
    content_hash: str
    size_bytes: int


class AgentRuntimePort(Protocol):
    """Invoke one immutable Agent node through a provider-neutral boundary."""

    async def invoke(self, agent: AgentVersion, input_payload: bytes) -> UnvalidatedAgentOutput:
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


class FindingBatchValidationPort(Protocol):
    """Convert untrusted canonical output into trusted domain Findings."""

    async def validate(self, payload: bytes) -> FindingBatch:
        """Apply schema, path, line, hunk, and evidence validation."""

        raise NotImplementedError


class AgentRunCompletionPort(Protocol):
    """Atomically persist trusted Findings, node success, and an outbox event."""

    async def complete_with_findings(self, run_id: str, findings: FindingBatch) -> None:
        """Complete only an OUTPUT_SAVED or VALIDATING run in one transaction."""

        raise NotImplementedError

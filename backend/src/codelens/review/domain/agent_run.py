import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from codelens.shared.domain.errors import DomainError


class InvalidAgentRunStateError(DomainError):
    """Raised when an AgentRun checkpoint transition is out of order."""

    code = "invalid_agent_run_state"


class AgentRunStatus(StrEnum):
    """Stable node states used for restart-safe Agent execution."""

    PENDING = "pending"
    RUNNING = "running"
    OUTPUT_SAVED = "output_saved"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    SKIPPED = "skipped"


def _run_id(
    task_id: str,
    agent_version: str,
    pass_index: int,
    shard_id: str,
    logical_attempt_group: str,
) -> str:
    identity = "\0".join((task_id, agent_version, str(pass_index), shard_id, logical_attempt_group))
    return f"run_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


@dataclass
class AgentRun:
    """Enforce durable output-before-validation checkpoints for one DAG node."""

    run_id: str
    task_id: str
    agent_version: str
    pass_index: int
    shard_id: str
    logical_attempt_group: str
    execution_attempts: int = 0
    output_artifact_ref: str | None = None
    output_artifact_hash: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error_code: str | None = None
    _status: AgentRunStatus = field(default=AgentRunStatus.PENDING, init=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        agent_version: str,
        pass_index: int,
        shard_id: str,
        logical_attempt_group: str,
    ) -> "AgentRun":
        """Create a stable node identity independent of execution retries."""

        if pass_index < 0:
            raise ValueError("Agent pass index cannot be negative")
        return cls(
            run_id=_run_id(
                task_id,
                agent_version,
                pass_index,
                shard_id,
                logical_attempt_group,
            ),
            task_id=task_id,
            agent_version=agent_version,
            pass_index=pass_index,
            shard_id=shard_id,
            logical_attempt_group=logical_attempt_group,
        )

    @property
    def status(self) -> AgentRunStatus:
        """Return the current checkpoint state without a public status setter."""

        return self._status

    def start(self) -> None:
        """Start one execution attempt from PENDING."""

        self._require(AgentRunStatus.PENDING)
        self.execution_attempts += 1
        self.error_code = None
        self._status = AgentRunStatus.RUNNING

    def save_output(
        self,
        artifact_ref: str,
        artifact_hash: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Checkpoint unvalidated canonical output before any schema validation."""

        self._require(AgentRunStatus.RUNNING)
        if len(artifact_hash) != 64:
            raise ValueError("Agent output Artifact hash must be SHA-256")
        self.output_artifact_ref = artifact_ref
        self.output_artifact_hash = artifact_hash
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self._status = AgentRunStatus.OUTPUT_SAVED

    def begin_validation(self) -> None:
        """Enter validation only after an output Artifact checkpoint exists."""

        self._require(AgentRunStatus.OUTPUT_SAVED)
        self._status = AgentRunStatus.VALIDATING

    def fail(self, error_code: str) -> None:
        """Record a stable failure from execution or validation."""

        if self._status not in {AgentRunStatus.RUNNING, AgentRunStatus.VALIDATING}:
            raise InvalidAgentRunStateError("AgentRun cannot fail from its current state")
        self.error_code = error_code
        self._status = AgentRunStatus.FAILED

    def timeout(self) -> None:
        """Record a bounded runtime timeout."""

        self._require(AgentRunStatus.RUNNING)
        self.error_code = "timed_out"
        self._status = AgentRunStatus.TIMED_OUT

    def cancel(self) -> None:
        """Cancel a pending or running node without opening a terminal node."""

        if self._status not in {AgentRunStatus.PENDING, AgentRunStatus.RUNNING}:
            raise InvalidAgentRunStateError("AgentRun cannot be canceled from its current state")
        self._status = AgentRunStatus.CANCELED

    def retry(self, *, max_attempts: int) -> None:
        """Return a transient failure to PENDING only while attempts remain."""

        if self._status not in {AgentRunStatus.FAILED, AgentRunStatus.TIMED_OUT}:
            raise InvalidAgentRunStateError("only failed AgentRuns can retry")
        if self.execution_attempts >= max_attempts:
            raise InvalidAgentRunStateError("AgentRun retry policy is exhausted")
        self._status = AgentRunStatus.PENDING

    def _require(self, expected: AgentRunStatus) -> None:
        if self._status is not expected:
            raise InvalidAgentRunStateError(
                f"AgentRun requires {expected}, current state is {self._status}"
            )

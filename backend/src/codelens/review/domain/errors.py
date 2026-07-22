from typing import Literal

from codelens.shared.domain.errors import DomainError


class AgentRuntimeError(DomainError):
    """Base class for stable provider-neutral Agent invocation failures."""

    code = "agent_runtime_error"

    def __init__(
        self,
        message: str,
        *,
        phase: Literal["investigation", "finalizing", "unknown"] = "unknown",
        reason_code: str = "unknown_agent_failure",
        retryable: bool = False,
        provider_status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.reason_code = reason_code
        self.retryable = retryable
        self.provider_status_code = provider_status_code

    def failure_metadata(self) -> dict[str, str]:
        """Return user-visible diagnostics without provider response content."""

        metadata = {
            "error_code": self.code,
            "reason_code": self.reason_code,
            "phase": self.phase,
            "retryable": str(self.retryable).lower(),
        }
        if self.provider_status_code is not None:
            metadata["provider_status_code"] = str(self.provider_status_code)
        return metadata


class TransientAgentRuntimeError(AgentRuntimeError):
    """Signal a bounded invocation failure that policy may retry."""

    code = "transient_agent_runtime_error"


class PermanentAgentOutputError(AgentRuntimeError):
    """Signal unusable model output that retry policy must handle explicitly."""

    code = "permanent_agent_output_error"


class AgentMaxTurnsExceededError(PermanentAgentOutputError):
    """Signal that an Agent used all allowed turns before returning an output."""

    code = "agent_max_turns_exceeded"

from codelens.shared.domain.errors import DomainError


class AgentRuntimeError(DomainError):
    """Base class for stable provider-neutral Agent invocation failures."""

    code = "agent_runtime_error"


class TransientAgentRuntimeError(AgentRuntimeError):
    """Signal a bounded invocation failure that policy may retry."""

    code = "transient_agent_runtime_error"


class PermanentAgentOutputError(AgentRuntimeError):
    """Signal unusable model output that retry policy must handle explicitly."""

    code = "permanent_agent_output_error"

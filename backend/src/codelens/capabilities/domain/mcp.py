"""Declarative bindings from stable CodeLens tools to future MCP adapters."""

import re
from dataclasses import dataclass

from codelens.capabilities.domain.models import ToolContractReference

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, order=True)
class McpToolBinding:
    """Freeze an MCP mapping without connecting to or executing the server.

    The binding only records a pre-approved stable CodeLens tool contract and
    bounded adapter configuration. Local non-egress tools must be scoped to the
    immutable Review Snapshot; live schema discovery is intentionally absent.
    """

    contract: ToolContractReference
    server_id: str
    remote_tool_name: str
    schema_hash: str
    snapshot_scoped: bool
    data_egress: bool
    timeout_seconds: float = 30.0
    max_result_bytes: int = 65_536

    def __post_init__(self) -> None:
        if _IDENTIFIER_PATTERN.fullmatch(self.server_id) is None:
            raise ValueError("MCP server ID is invalid")
        if _IDENTIFIER_PATTERN.fullmatch(self.remote_tool_name) is None:
            raise ValueError("MCP remote tool name is invalid")
        if _SHA256_PATTERN.fullmatch(self.schema_hash) is None:
            raise ValueError("MCP schema hash must be a lowercase SHA-256 digest")
        if not self.snapshot_scoped and not self.data_egress:
            raise ValueError("Local MCP code tools must be Snapshot scoped")
        if self.timeout_seconds <= 0:
            raise ValueError("MCP timeout must be positive")
        if self.max_result_bytes <= 0:
            raise ValueError("MCP result-size limit must be positive")

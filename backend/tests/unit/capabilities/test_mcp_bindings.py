import pytest

from codelens.capabilities.domain.mcp import McpToolBinding
from codelens.capabilities.domain.models import ToolContractReference


def test_mcp_binding_requires_explicit_stable_contract_and_schema_hash() -> None:
    with pytest.raises(ValueError, match="schema hash"):
        McpToolBinding(
            contract=ToolContractReference("symbol_search", 1),
            server_id="local-code-index",
            remote_tool_name="search",
            schema_hash="",
            snapshot_scoped=True,
            data_egress=False,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("server_id", "", "server ID"),
        ("remote_tool_name", "", "remote tool name"),
        ("timeout_seconds", 0.0, "timeout"),
        ("max_result_bytes", 0, "result-size"),
    ],
)
def test_mcp_binding_rejects_incomplete_or_unbounded_configuration(
    field: str,
    value: str | float | int,
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "contract": ToolContractReference("symbol_search", 1),
        "server_id": "local-code-index",
        "remote_tool_name": "search",
        "schema_hash": "a" * 64,
        "snapshot_scoped": True,
        "data_egress": False,
        "timeout_seconds": 30.0,
        "max_result_bytes": 65_536,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        McpToolBinding(**arguments)  # type: ignore[arg-type]


def test_local_mcp_binding_must_be_snapshot_scoped() -> None:
    with pytest.raises(ValueError, match="Snapshot scoped"):
        McpToolBinding(
            contract=ToolContractReference("symbol_search", 1),
            server_id="local-code-index",
            remote_tool_name="search",
            schema_hash="a" * 64,
            snapshot_scoped=False,
            data_egress=False,
        )


def test_mcp_binding_is_only_frozen_configuration() -> None:
    binding = McpToolBinding(
        contract=ToolContractReference("symbol_search", 1),
        server_id="local-code-index",
        remote_tool_name="search",
        schema_hash="a" * 64,
        snapshot_scoped=True,
        data_egress=False,
    )

    assert binding.timeout_seconds == 30.0
    assert binding.max_result_bytes == 65_536
    assert not hasattr(binding, "connect")
    assert not hasattr(binding, "execute")

import json
from pathlib import Path

from codelens.reviewer_catalog.infrastructure.file_provider_config import (
    FilesystemModelProviderConfigAdapter,
)


async def test_legacy_gateway_catalog_loads_with_execution_limit_defaults(
    tmp_path: Path,
) -> None:
    secrets_directory = tmp_path / "secrets"
    secrets_directory.mkdir()
    (secrets_directory / "model-gateways.json").write_text(
        json.dumps(
            {
                "version": 1,
                "active_gateway_id": "gateway_legacy",
                "gateways": [
                    {
                        "gateway_id": "gateway_legacy",
                        "name": "Legacy gateway",
                        "api_key": "sk-legacy",
                        "model": "legacy-model",
                        "base_url": "https://model.example/v1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = await FilesystemModelProviderConfigAdapter(tmp_path).load_catalog()

    gateway = catalog.gateways[0]
    assert gateway.agent_timeout == 3600
    assert gateway.max_agent_turns == 500
    assert gateway.max_tool_calls == 500
    assert gateway.max_identical_tool_results == 3
    assert gateway.tool_timeout_seconds == 30
    assert gateway.no_progress_rounds_threshold == 10

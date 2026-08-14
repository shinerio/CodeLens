"""Contract tests for the tool limits API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_tool_limits_returns_defaults_initially(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/settings/tool-limits")
    assert response.status_code == 200
    data = response.json()
    assert data["max_results"] == 200
    assert data["max_read_bytes"] == 65536
    assert data["max_scan_bytes"] == 1048576
    assert data["max_source_bytes"] == 1048576
    assert data["max_lines"] == 1000
    assert data["max_path_chars"] == 1024
    assert data["max_pattern_chars"] == 512
    assert data["regex_timeout_seconds"] == 30.0
    assert data["comment_batch_size"] == 20
    assert data["short_text_max"] == 240
    assert data["long_text_max"] == 8000
    assert data["task_summary_max"] == 8000
    assert data["context_compaction_enabled"] is True
    assert data["context_compaction_trigger_bytes"] == 131072
    assert data["context_compaction_target_bytes"] == 32768
    assert data["context_compaction_keep_recent_evidence_results"] == 6


def test_tool_limits_update_and_persist(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        update = client.put(
            "/api/settings/tool-limits",
            json={
                "max_results": 500,
                "max_read_bytes": 131072,
                "max_scan_bytes": 2097152,
                "max_source_bytes": 2097152,
                "max_lines": 1000,
                "max_path_chars": 2048,
                "max_pattern_chars": 1024,
                "regex_timeout_seconds": 60.0,
                "comment_batch_size": 50,
                "short_text_max": 480,
                "long_text_max": 16000,
                "task_summary_max": 16000,
                "context_compaction_enabled": False,
                "context_compaction_trigger_bytes": 262144,
                "context_compaction_target_bytes": 131072,
                "context_compaction_keep_recent_evidence_results": 4,
            },
        )
        assert update.status_code == 200
        assert update.json()["max_results"] == 500
        assert update.json()["context_compaction_enabled"] is False

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get("/api/settings/tool-limits")
        assert persisted.json()["max_results"] == 500


def test_tool_limits_partial_update(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        update = client.put(
            "/api/settings/tool-limits",
            json={"max_results": 300},
        )
        assert update.status_code == 200
        data = update.json()
        assert data["max_results"] == 300
        assert data["max_lines"] == 1000  # unchanged


def test_tool_limits_rejects_invalid_range(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        too_small = client.put("/api/settings/tool-limits", json={"max_results": 0})
        too_large = client.put("/api/settings/tool-limits", json={"max_results": 99999})
        assert too_small.status_code == 422
        assert too_large.status_code == 422


def test_tool_limits_rejects_compaction_target_not_smaller_than_trigger(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.put(
            "/api/settings/tool-limits",
            json={
                "context_compaction_trigger_bytes": 65536,
                "context_compaction_target_bytes": 65536,
            },
        )

    assert response.status_code == 422


def test_reset_all_restores_defaults(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        # Modify various settings
        client.put("/api/settings/tool-limits", json={"max_results": 999})
        client.put("/api/settings/logging", json={"level": "debug"})
        client.put("/api/settings/repositories", json={"recent_repository_limit": 5})
        client.put(
            "/api/settings/instruction-files",
            json={"root_max_lines": 1000, "nested_max_lines": 500},
        )
        client.put(
            "/api/settings/review-completion",
            json={"max_incomplete_review_retries": 10},
        )
        client.post(
            "/api/settings/model-gateways",
            json={
                "name": "Reset gateway",
                "api_key": "sk-reset-test-secret",
                "model": "gpt-reset",
                "base_url": "https://reset.example/v1",
                "agent_timeout": 900,
                "max_agent_turns": 80,
                "max_tool_calls": 240,
            },
        )

        # Reset all
        reset = client.post("/api/settings/reset-all", json={})
        assert reset.status_code == 200
        data = reset.json()

        # Verify all settings are reset to defaults
        assert data["tool_limits"]["max_results"] == 200
        assert data["logging"]["level"] == "info"
        assert data["recent_repositories"]["recent_repository_limit"] == 10
        assert data["instruction_files"]["root_max_lines"] == 1000
        assert data["instruction_files"]["nested_max_lines"] == 500
        assert data["review_completion"]["max_incomplete_review_retries"] == 3
        reset_gateway = data["model_gateways"]["gateways"][0]
        assert reset_gateway["agent_timeout"] == 3600
        assert reset_gateway["max_agent_turns"] == 500
        assert reset_gateway["max_tool_calls"] == 500

        # Verify persistence
        tool_limits = client.get("/api/settings/tool-limits")
        assert tool_limits.json()["max_results"] == 200

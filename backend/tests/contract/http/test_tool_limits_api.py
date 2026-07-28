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
    assert data["max_lines"] == 500
    assert data["max_path_chars"] == 1024
    assert data["max_pattern_chars"] == 512
    assert data["regex_timeout_seconds"] == 30.0
    assert data["comment_batch_size"] == 20
    assert data["reviewed_files_batch"] == 2000
    assert data["short_text_max"] == 240
    assert data["long_text_max"] == 8000
    assert data["task_summary_max"] == 8000


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
                "reviewed_files_batch": 5000,
                "short_text_max": 480,
                "long_text_max": 16000,
                "task_summary_max": 16000,
            },
        )
        assert update.status_code == 200
        assert update.json()["max_results"] == 500

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
        assert data["max_lines"] == 500  # unchanged


def test_tool_limits_rejects_invalid_range(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        too_small = client.put("/api/settings/tool-limits", json={"max_results": 0})
        too_large = client.put("/api/settings/tool-limits", json={"max_results": 99999})
        assert too_small.status_code == 422
        assert too_large.status_code == 422


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

        # Reset all
        reset = client.post("/api/settings/reset-all", json={})
        assert reset.status_code == 200
        data = reset.json()

        # Verify all settings are reset to defaults
        assert data["tool_limits"]["max_results"] == 200
        assert data["logging"]["level"] == "info"
        assert data["recent_repositories"]["recent_repository_limit"] == 10
        assert data["instruction_files"]["root_max_lines"] == 500
        assert data["instruction_files"]["nested_max_lines"] == 200
        assert data["review_completion"]["max_incomplete_review_retries"] == 3

        # Verify persistence
        tool_limits = client.get("/api/settings/tool-limits")
        assert tool_limits.json()["max_results"] == 200

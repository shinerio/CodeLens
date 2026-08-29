"""Contract tests for the node settings API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_node_settings_returns_defaults_initially(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/settings/node-limits")
    assert response.status_code == 200
    data = response.json()
    assert data["memory_limit_mb"] == 2048
    assert data["memory_check_interval_seconds"] == 5.0
    assert data["memory_cleanup_threshold_ratio"] == 0.85
    assert data["memory_reject_threshold_ratio"] == 0.95
    assert data["max_active_reviews"] == 4
    assert data["max_active_agent_runs"] == 8
    assert data["max_agent_runs_per_review"] == 4


def test_node_settings_update_and_persist(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        update = client.put(
            "/api/settings/node-limits",
            json={
                "memory_limit_mb": 4096,
                "memory_check_interval_seconds": 10.0,
                "memory_cleanup_threshold_ratio": 0.8,
                "memory_reject_threshold_ratio": 0.9,
                "max_active_reviews": 8,
                "max_active_agent_runs": 16,
                "max_agent_runs_per_review": 6,
            },
        )
        assert update.status_code == 200
        assert update.json()["memory_limit_mb"] == 4096
        assert update.json()["max_active_agent_runs"] == 16

    # Persisted across a fresh app instance (simulates next restart read)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get("/api/settings/node-limits")
        assert persisted.json()["memory_limit_mb"] == 4096
        assert persisted.json()["max_active_reviews"] == 8


def test_node_settings_partial_update(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        update = client.put("/api/settings/node-limits", json={"memory_limit_mb": 8192})
        assert update.status_code == 200
        data = update.json()
        assert data["memory_limit_mb"] == 8192
        assert data["max_active_reviews"] == 4  # unchanged


def test_node_settings_rejects_invalid_range(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        too_small = client.put("/api/settings/node-limits", json={"memory_limit_mb": 100})
        assert too_small.status_code == 422


def test_node_settings_rejects_threshold_violation(tmp_path: Path) -> None:
    """cleanup >= reject is rejected at the domain layer (422), not just DTO range."""
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.put(
            "/api/settings/node-limits",
            json={
                "memory_cleanup_threshold_ratio": 0.9,
                "memory_reject_threshold_ratio": 0.9,
            },
        )
        assert response.status_code == 422


def test_node_settings_rejects_per_review_exceeds_global(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.put(
            "/api/settings/node-limits",
            json={"max_active_agent_runs": 4, "max_agent_runs_per_review": 8},
        )
        assert response.status_code == 422


def test_reset_all_restores_node_settings_defaults(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        client.put("/api/settings/node-limits", json={"memory_limit_mb": 8192})
        reset = client.post("/api/settings/reset-all", json={})
        assert reset.status_code == 200
        assert reset.json()["node_settings"]["memory_limit_mb"] == 2048

        persisted = client.get("/api/settings/node-limits")
        assert persisted.json()["memory_limit_mb"] == 2048

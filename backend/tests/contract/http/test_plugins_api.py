from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_plugin_response_exposes_copied_v2_policy(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/plugins/local")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_api_version"] == "2"
    assert payload["compatibility_status"] == "compatible"
    assert payload["config_revision"] == 1
    assert payload["config"]["reviewer_selection"] == {
        "mode": "fixed",
        "reviewer_versions": ["correctness:v2"],
    }
    assert "selected_agents" not in payload["config"]
    assert payload["profile_source"] is None


def test_plugin_config_persists_profile_provenance_beside_policy(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.put(
            "/api/plugins/local/trigger/config",
            json={
                "config": {
                    "reviewer_selection": {"mode": "adaptive"},
                },
                "profile_source": {
                    "profile_id": "profile-balanced",
                    "profile_name": "Balanced Review",
                    "profile_revision": 3,
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["config"]["reviewer_selection"] == {"mode": "adaptive"}
    assert "profile_source" not in payload["config"]
    assert payload["profile_source"]["profile_id"] == "profile-balanced"
    assert payload["profile_source"]["profile_name"] == "Balanced Review"
    assert payload["profile_source"]["profile_revision"] == 3
    assert payload["profile_source"]["copied_at"] is not None


def test_plugin_config_rejects_mixed_adaptive_fields(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.put(
            "/api/plugins/local/trigger/config",
            json={
                "config": {
                    "reviewer_selection": {
                        "mode": "adaptive",
                        "reviewer_versions": ["security:v2"],
                    }
                }
            },
        )

    assert response.status_code == 400, response.text

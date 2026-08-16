from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_runtime_logging_settings_are_readable_and_persistent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        initial = client.get("/api/settings/logging")
        updated = client.put(
            "/api/settings/logging",
            json={"level": "debug", "model_output_enabled": False},
        )

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get("/api/settings/logging")

    expected_initial = {
        "default_level": "info",
        "level": "info",
        "model_output_enabled": True,
    }
    expected_updated = {
        "default_level": "info",
        "level": "debug",
        "model_output_enabled": False,
    }
    assert initial.json() == expected_initial
    assert updated.json() == expected_updated
    assert persisted.json() == expected_updated

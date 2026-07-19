from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_runtime_log_level_setting_is_readable_and_persistent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        initial = client.get("/api/settings/logging")
        updated = client.put("/api/settings/logging", json={"level": "debug"})

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get("/api/settings/logging")

    assert initial.json() == {"level": "info"}
    assert updated.json() == {"level": "debug"}
    assert persisted.json() == {"level": "debug"}

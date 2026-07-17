from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_health_reports_ready(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path)))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "auth": "none"}

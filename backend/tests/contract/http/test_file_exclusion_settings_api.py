from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_file_exclusion_settings_are_not_exposed_over_http(tmp_path: Path) -> None:
    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data")),
        base_url="http://127.0.0.1:8765",
    ) as client:
        get_response = client.get("/api/settings/file-exclusions")
        put_response = client.put(
            "/api/settings/file-exclusions",
            json={"suffixes": [".log"]},
        )

    assert get_response.status_code == 404
    assert put_response.status_code == 404

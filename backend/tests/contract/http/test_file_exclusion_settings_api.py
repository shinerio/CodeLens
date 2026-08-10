from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_file_exclusion_settings_default_to_binary_exclusion(tmp_path: Path) -> None:
    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data")),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = client.get("/api/settings/file-exclusions")

    assert response.status_code == 200
    assert response.json() == {
        "exclude_binary": True,
        "path_regexes": [],
        "suffixes": [],
    }


def test_file_exclusion_settings_support_atomic_partial_updates(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        updated = client.put(
            "/api/settings/file-exclusions",
            json={"suffixes": [".MAP", ".map"], "path_regexes": ["generated/"]},
        )
        persisted = client.get("/api/settings/file-exclusions")

    assert updated.status_code == 200
    assert persisted.json() == {
        "exclude_binary": True,
        "path_regexes": ["generated/"],
        "suffixes": [".map"],
    }


def test_file_exclusion_settings_reject_invalid_regex(tmp_path: Path) -> None:
    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data")),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = client.put("/api/settings/file-exclusions", json={"path_regexes": ["["]})

    assert response.status_code == 422

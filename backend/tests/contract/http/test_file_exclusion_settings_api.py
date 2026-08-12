from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_file_exclusion_settings_manage_the_web_overlay(tmp_path: Path) -> None:
    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data")),
        base_url="http://127.0.0.1:8765",
    ) as client:
        initial = client.get("/api/settings/file-exclusions")
        updated = client.put(
            "/api/settings/file-exclusions",
            json={"suffixes": [".CUSTOM", ".custom"], "path_regexes": ["^generated/"]},
        )
        persisted = client.get("/api/settings/file-exclusions")

    assert initial.status_code == 200
    assert initial.json() == {"exclude_binary": True, "path_regexes": [], "suffixes": []}
    assert updated.status_code == 200
    assert persisted.json() == {
        "exclude_binary": True,
        "path_regexes": ["^generated/"],
        "suffixes": [".custom"],
    }

from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_recent_repository_limit_is_validated_and_persistent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        initial = client.get("/api/settings/repositories")
        updated = client.put(
            "/api/settings/repositories",
            json={"recent_repository_limit": 4},
        )
        too_small = client.put(
            "/api/settings/repositories",
            json={"recent_repository_limit": 0},
        )
        too_large = client.put(
            "/api/settings/repositories",
            json={"recent_repository_limit": 21},
        )

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get("/api/settings/repositories")

    assert initial.json() == {"recent_repository_limit": 10}
    assert updated.json() == {"recent_repository_limit": 4}
    assert too_small.status_code == 422
    assert too_large.status_code == 422
    assert persisted.json() == {"recent_repository_limit": 4}

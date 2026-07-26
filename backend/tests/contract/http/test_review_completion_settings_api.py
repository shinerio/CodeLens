from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_incomplete_review_retry_limit_is_validated_and_persistent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        initial = client.get("/api/settings/review-completion")
        updated = client.put(
            "/api/settings/review-completion",
            json={"max_incomplete_review_retries": 4},
        )
        too_small = client.put(
            "/api/settings/review-completion",
            json={"max_incomplete_review_retries": -1},
        )
        too_large = client.put(
            "/api/settings/review-completion",
            json={"max_incomplete_review_retries": 21},
        )

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get("/api/settings/review-completion")

    assert initial.json() == {"max_incomplete_review_retries": 3}
    assert updated.json() == {"max_incomplete_review_retries": 4}
    assert too_small.status_code == 422
    assert too_large.status_code == 422
    assert persisted.json() == {"max_incomplete_review_retries": 4}

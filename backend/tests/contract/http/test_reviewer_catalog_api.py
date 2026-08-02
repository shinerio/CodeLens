from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_catalog_exposes_only_public_reviewer_versions(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", repository_roots=(tmp_path,))

    with TestClient(
        create_app(settings), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get("/api/reviewer-catalog")

    assert response.status_code == 200
    entries = response.json()
    references = {item["reference"] for item in entries}
    assert "general:v1" in references
    assert "security:v1" in references
    assert "correctness:v1" not in references
    assert "review-planner:v1" not in references
    assert all(item["capability_readiness"] == "ready" for item in entries)

from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_instruction_line_limits_are_validated_and_persistent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        initial = client.get("/api/settings/instruction-files")
        updated = client.put(
            "/api/settings/instruction-files",
            json={"root_max_lines": 800, "nested_max_lines": 240},
        )
        inverted = client.put(
            "/api/settings/instruction-files",
            json={"root_max_lines": 100, "nested_max_lines": 200},
        )
        too_large = client.put(
            "/api/settings/instruction-files",
            json={"root_max_lines": 10001, "nested_max_lines": 200},
        )

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get("/api/settings/instruction-files")

    assert initial.json() == {"root_max_lines": 1000, "nested_max_lines": 500}
    assert updated.json() == {"root_max_lines": 800, "nested_max_lines": 240}
    assert inverted.status_code == 422
    assert too_large.status_code == 422
    assert persisted.json() == {"root_max_lines": 800, "nested_max_lines": 240}


def test_reviewer_prompt_http_contract_requires_the_canonical_v2_version(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        missing_version = client.get("/api/reviewer-prompts/correctness?locale=en")
        version_two = client.get("/api/reviewer-prompts/correctness?version=2&locale=en")
        updated = client.put(
            "/api/reviewer-prompts/correctness?version=2&locale=en",
            json={"prompt": "Custom correctness v2 prompt."},
        )

    assert missing_version.status_code == 200
    assert missing_version.json()["version"] == 2
    assert version_two.status_code == 200
    assert version_two.json()["version"] == 2
    assert updated.json()["prompt"] == "Custom correctness v2 prompt."


def test_reviewer_prompt_http_contract_rejects_unknown_versions(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/reviewer-prompts/correctness?version=999&locale=en")

    assert response.status_code == 404

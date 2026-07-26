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

    assert initial.json() == {"root_max_lines": 500, "nested_max_lines": 200}
    assert updated.json() == {"root_max_lines": 800, "nested_max_lines": 240}
    assert inverted.status_code == 422
    assert too_large.status_code == 422
    assert persisted.json() == {"root_max_lines": 800, "nested_max_lines": 240}

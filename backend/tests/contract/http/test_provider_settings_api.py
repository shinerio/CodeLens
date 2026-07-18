import stat
from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_openai_settings_are_saved_without_returning_the_api_key(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    api_key = "sk-test-secret-never-return"

    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1:8765",
    ) as client:
        initial = client.get("/api/settings/openai")
        saved = client.put(
            "/api/settings/openai",
            json={
                "api_key": api_key,
                "model": "gpt-test",
                "base_url": "http://model-gateway.example:8080",
            },
        )

    assert initial.status_code == 200
    assert initial.json() == {
        "is_configured": False,
        "model": None,
        "base_url": None,
    }
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "is_configured": True,
        "model": "gpt-test",
        "base_url": "http://model-gateway.example:8080",
    }
    assert api_key not in saved.text

    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1:8765",
    ) as restarted_client:
        persisted = restarted_client.get("/api/settings/openai")

    assert persisted.json() == saved.json()
    secret_directory = settings.data_dir / "secrets"
    secret_file = secret_directory / "model-gateways.json"
    assert secret_file.is_file()
    assert api_key in secret_file.read_text(encoding="utf-8")
    assert stat.S_IMODE(secret_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600


def test_openai_settings_reject_an_empty_api_key(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = client.put(
            "/api/settings/openai",
            json={
                "api_key": "   ",
                "model": "gpt-test",
                "base_url": "https://model-gateway.example/v1",
            },
        )

    assert response.status_code == 422
    assert not (settings.data_dir / "secrets" / "openai-provider.json").exists()

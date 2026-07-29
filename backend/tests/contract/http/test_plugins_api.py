from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_trigger_config_installs_reports_and_removes_repository_hooks(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    hook_path = git_repository / ".git" / "hooks" / "post-commit"

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        enabled = client.put(
            "/api/plugins/local/trigger/enable",
            json={},
        )
        configured = client.put(
            "/api/plugins/local/trigger/config",
            json={
                "config": {
                    "repository_paths": [str(git_repository)],
                    "events": ["post-commit"],
                }
            },
        )
        installed_status = client.get(
            "/api/plugins/local/trigger/hook-status",
        )

        assert configured.status_code == 200, configured.text
        assert hook_path.is_file()
        assert installed_status.status_code == 200, installed_status.text
        assert installed_status.json() == {
            "is_installed": True,
            "hook_path": str(hook_path),
            "repository_path": str(git_repository),
            "repositories": [
                {
                    "repository_path": str(git_repository),
                    "hooks": {"post-commit": True},
                    "is_installed": True,
                }
            ],
        }

        removed = client.put(
            "/api/plugins/local/trigger/config",
            json={"config": {"repository_paths": []}},
        )

    assert enabled.status_code == 200, enabled.text
    assert removed.status_code == 200, removed.text
    assert not hook_path.exists()


def test_enabling_trigger_installs_hooks_for_an_already_configured_repository(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    hook_path = git_repository / ".git" / "hooks" / "post-commit"

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        configured = client.put(
            "/api/plugins/local/trigger/config",
            json={
                "config": {
                    "repository_paths": [str(git_repository)],
                    "events": ["post-commit"],
                }
            },
        )

        assert configured.status_code == 200, configured.text
        assert not hook_path.exists()

        enabled = client.put(
            "/api/plugins/local/trigger/enable",
            json={},
        )
        installed_status = client.get(
            "/api/plugins/local/trigger/hook-status",
        )

    assert enabled.status_code == 200, enabled.text
    assert hook_path.is_file()
    assert installed_status.status_code == 200, installed_status.text
    assert installed_status.json()["repositories"][0]["is_installed"] is True

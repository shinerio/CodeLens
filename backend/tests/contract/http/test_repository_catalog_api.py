import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_repository_catalog_rejects_a_target_outside_selectable_branches(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            "/api/repositories/catalog",
            json={
                "path": str(git_repository),
                "target_ref": "refs/tags/not-a-selectable-branch",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_repository"


def test_filesystem_browser_starts_at_system_roots_and_marks_git_directories(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        roots = client.post("/api/repositories/browse", json={"path": None})
        listing = client.post(
            "/api/repositories/browse",
            json={"path": str(git_repository.parent)},
        )

    assert roots.status_code == 200, roots.text
    if os.name == "nt":
        assert any(root[1:3] == ":\\" for root in roots.json()["roots"])
    else:
        assert "/" in roots.json()["roots"]
    assert listing.status_code == 200, listing.text
    assert listing.json()["current_path"] == str(git_repository.parent.resolve())
    repository_entry = next(
        entry
        for entry in listing.json()["directories"]
        if entry["path"] == str(git_repository.resolve())
    )
    assert repository_entry == {
        "name": git_repository.name,
        "path": str(git_repository.resolve()),
        "is_git_repository": True,
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
@pytest.mark.skipif(os.geteuid() == 0, reason="root bypass file permission checks")
def test_filesystem_browser_skips_directories_the_current_user_cannot_access(
    tmp_path: Path,
) -> None:
    visible = tmp_path / "visible"
    inaccessible = tmp_path / "inaccessible"
    visible.mkdir()
    inaccessible.mkdir()
    inaccessible.chmod(0)
    app = create_app(Settings(data_dir=tmp_path / "data"))

    try:
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            listing = client.post(
                "/api/repositories/browse",
                json={"path": str(tmp_path)},
            )
    finally:
        inaccessible.chmod(0o700)

    assert listing.status_code == 200, listing.text
    names = {entry["name"] for entry in listing.json()["directories"]}
    assert "visible" in names
    assert "inaccessible" not in names

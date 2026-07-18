import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app
from tests.fixtures.git_repository import _run_git


def _add_catalog_history(repository: Path) -> None:
    for index in range(12):
        (repository / "history.txt").write_text(f"revision {index}\n", encoding="utf-8")
        _run_git("-C", str(repository), "add", "history.txt")
        _run_git("-C", str(repository), "commit", "-m", f"catalog commit {index:02d}")
    _run_git("-C", str(repository), "branch", "feature/catalog")


def test_repository_catalog_lists_all_branches_and_paginates_commit_summaries(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _add_catalog_history(git_repository)
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        first = client.post(
            "/api/repositories/catalog",
            json={"path": str(git_repository), "commit_offset": 0, "commit_limit": 10},
        )
        second = client.post(
            "/api/repositories/catalog",
            json={"path": str(git_repository), "commit_offset": 10, "commit_limit": 10},
        )

    assert first.status_code == 200, first.text
    assert {branch["name"] for branch in first.json()["branches"]} >= {
        "main",
        "feature/catalog",
    }
    assert len(first.json()["commits"]) == 10
    assert first.json()["next_commit_offset"] == 10
    newest = first.json()["commits"][0]
    assert newest["short_oid"] == newest["oid"][: len(newest["short_oid"])]
    assert newest["author"] == "Test User"
    assert newest["message"] == "catalog commit 11"
    assert newest["committed_at"]
    assert second.status_code == 200, second.text
    assert len(second.json()["commits"]) >= 3
    assert {
        commit["oid"] for commit in first.json()["commits"]
    }.isdisjoint(commit["oid"] for commit in second.json()["commits"])


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

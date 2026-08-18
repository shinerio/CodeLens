from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def _profile_request(name: str, *, is_default: bool = False) -> dict[str, object]:
    return {
        "name": name,
        "is_default": is_default,
        "reviewer_selection": {"mode": "adaptive"},
    }


def test_review_profile_crud_is_strict_and_reports_revision_conflicts(
    tmp_path: Path,
) -> None:
    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data")),
        base_url="http://127.0.0.1:8765",
    ) as client:
        initial = client.get("/api/review-profiles")
        invalid = client.post(
            "/api/review-profiles", json={**_profile_request("Invalid"), "unknown": True}
        )
        created = client.post("/api/review-profiles", json=_profile_request("Deep"))
        profile = created.json()
        updated = client.put(
            f"/api/review-profiles/{profile['profile_id']}",
            json={**_profile_request("Deep Default", is_default=True), "revision": 1},
        )
        stale = client.put(
            f"/api/review-profiles/{profile['profile_id']}",
            json={**_profile_request("Stale"), "revision": 1},
        )

    assert initial.json()[0]["profile_id"] == "profile-balanced"
    assert invalid.status_code == 422
    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert stale.status_code == 409
    assert stale.json()["code"] == "review_profile_revision_conflict"


def test_copy_starts_independent_non_default_revision_one(tmp_path: Path) -> None:
    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data")),
        base_url="http://127.0.0.1:8765",
    ) as client:
        copied = client.post(
            "/api/review-profiles/profile-balanced/copies", json={"name": "Copied"}
        )
        source = client.put(
            "/api/review-profiles/profile-balanced",
            json={
                "revision": 1,
                "name": "Changed",
                "is_default": True,
                "reviewer_selection": {
                    "mode": "fixed",
                    "reviewer_versions": ["security:v2"],
                },
            },
        )
        profiles = client.get("/api/review-profiles").json()

    copy = copied.json()
    persisted_copy = next(item for item in profiles if item["profile_id"] == copy["profile_id"])
    assert copied.status_code == 201
    assert copy["profile_id"] != "profile-balanced"
    assert copy["revision"] == 1
    assert copy["is_default"] is False
    assert source.status_code == 200
    assert persisted_copy["reviewer_selection"] == {"mode": "adaptive"}

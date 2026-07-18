import subprocess
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingBatch,
    FindingDisposition,
    FindingSeverity,
    RuleReference,
    SourceLocation,
)
from codelens.interface.http.app import create_app
from codelens.review.infrastructure.repositories import SqlCheckpointStore
from tests.fixtures.git_repository import _run_git


def _commit(repository: Path, branch: str, content: str) -> str:
    _run_git("-C", str(repository), "switch", "-c", branch)
    (repository / "feature.py").write_text(content, encoding="utf-8")
    _run_git("-C", str(repository), "add", "feature.py")
    _run_git("-C", str(repository), "commit", "-m", branch)
    oid = _run_git("-C", str(repository), "rev-parse", "HEAD").stdout.decode().strip()
    _run_git("-C", str(repository), "switch", "main")
    return oid


def _prepared_repository(repository: Path) -> tuple[str, str, str]:
    main_oid = _run_git("-C", str(repository), "rev-parse", "main").stdout.decode().strip()
    first_oid = _commit(repository, "feature-one", "value = 1\n")
    second_oid = _commit(repository, "feature-two", "value = 2\n")
    return main_oid, first_oid, second_oid


def _settings(tmp_path: Path, repository_root: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        repository_roots=(repository_root,),
    )


def _request(repository: Path, scope: dict[str, object]) -> dict[str, object]:
    return {
        "repository_path": str(repository),
        "scope": scope,
        "selected_agents": ["correctness:v1"],
        "mode": "review",
    }


def _run_git_safe(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *arguments],
        check=True,
        capture_output=True,
        timeout=30.0,
    )


def test_startup_removes_only_verified_orphan_input_artifacts(tmp_path: Path) -> None:
    settings = _settings(tmp_path, tmp_path)
    artifact_root = settings.data_dir / "artifacts" / "inputs"
    artifact_root.mkdir(parents=True)
    orphan = artifact_root / ("input_" + "a" * 32)
    staging = artifact_root / (".input_" + "b" * 32 + ".tmp")
    unrelated = artifact_root / "operator-note.txt"
    orphan.write_bytes(b"orphan")
    staging.write_bytes(b"partial")
    unrelated.write_text("keep", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200

    assert not orphan.exists()
    assert not staging.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("scope", "scope_type"),
    [
        (
            {
                "type": "branch",
                "base_ref": "main",
                "target_ref": "feature-one",
                "include_workspace_changes": False,
            },
            "branch",
        ),
        (
            {
                "type": "commit",
                "base_commit": "main",
                "target_ref": "feature-one",
                "include_workspace_changes": False,
            },
            "commit",
        ),
        ({"type": "uncommitted"}, "uncommitted"),
        (
            {
                "type": "full",
                "target_ref": "feature-one",
                "include_workspace_changes": False,
            },
            "full",
        ),
    ],
)
def test_create_review_pins_all_scope_types(
    tmp_path: Path,
    git_repository: Path,
    scope: dict[str, object],
    scope_type: str,
) -> None:
    _prepared_repository(git_repository)
    (git_repository / "README.md").write_text("# dirty fixture\n", encoding="utf-8")
    settings = _settings(tmp_path, tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/reviews", json=_request(git_repository, scope))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["task_id"].startswith("review_")
    assert body["status"] == "created"
    assert body["scope_type"] == scope_type
    assert len(body["base_oid"]) == 40
    assert len(body["head_oid"]) == 40
    assert body["selected_agents"] == ["correctness:v1"]
    assert body["worktree_status"] == "pending"
    assert "worktree_path" not in body
    assert "artifact_path" not in body
    if scope_type == "uncommitted":
        artifact_files = tuple((settings.data_dir / "artifacts" / "inputs").glob("input_*"))
        assert len(artifact_files) == 1
        with TestClient(create_app(settings)) as restarted_client:
            assert restarted_client.get("/api/health").status_code == 200
        assert artifact_files[0].exists()


def test_repository_inspection_and_same_repository_reviews_are_independent(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _main_oid, first_oid, second_oid = _prepared_repository(git_repository)
    app = create_app(_settings(tmp_path, tmp_path))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        inspection = client.post(
            "/api/repositories/inspect",
            json={"path": str(git_repository)},
        )
        first = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-one",
                    "include_workspace_changes": False,
                },
            ),
        )
        second = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-two",
                    "include_workspace_changes": False,
                },
            ),
        )

    assert inspection.status_code == 200, inspection.text
    descriptor = inspection.json()
    assert descriptor["repository_id"].startswith("repository_")
    assert len(descriptor["repository_realpath_hash"]) == 64
    assert len(descriptor["git_common_dir_hash"]) == 64
    assert descriptor["display_path"] == str(git_repository.resolve())
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] != second.json()["task_id"]
    assert first.json()["head_oid"] == first_oid
    assert second.json()["head_oid"] == second_oid


@pytest.mark.parametrize(
    "mutation",
    [
        {"selected_agents": []},
        {"mode": "fix"},
        {"artifact_id": "/tmp/provider-output.json"},
        {"worktree_id": "/tmp/owned-checkout"},
    ],
)
def test_create_review_rejects_unsupported_or_path_shaped_control_input(
    tmp_path: Path,
    git_repository: Path,
    mutation: dict[str, object],
) -> None:
    _prepared_repository(git_repository)
    payload = _request(
        git_repository,
        {
            "type": "branch",
            "base_ref": "main",
            "target_ref": "feature-one",
            "include_workspace_changes": False,
        },
    )
    payload.update(mutation)

    with TestClient(
        create_app(_settings(tmp_path, tmp_path)),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = client.post("/api/reviews", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("path_kind", ["missing", "non_git", "outside"])
def test_repository_path_must_be_an_allowed_git_root(
    tmp_path: Path,
    git_repository: Path,
    path_kind: str,
) -> None:
    _prepared_repository(git_repository)
    non_git = tmp_path / "non-git"
    non_git.mkdir()
    candidates = {
        "missing": tmp_path / "missing",
        "non_git": non_git,
        "outside": tmp_path.parent,
    }
    app = create_app(_settings(tmp_path, tmp_path))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            "/api/reviews",
            json=_request(
                candidates[path_kind],
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-one",
                    "include_workspace_changes": False,
                },
            ),
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_repository"


@pytest.mark.parametrize("target_ref", ["unknown", "ambiguous"])
def test_create_review_rejects_unknown_or_ambiguous_refs(
    tmp_path: Path,
    git_repository: Path,
    target_ref: str,
) -> None:
    _prepared_repository(git_repository)
    _run_git("-C", str(git_repository), "branch", "ambiguous", "feature-one")
    _run_git("-C", str(git_repository), "tag", "ambiguous", "main")
    app = create_app(_settings(tmp_path, tmp_path))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": target_ref,
                    "include_workspace_changes": False,
                },
            ),
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_repository"


def test_workspace_overlay_requires_target_to_match_current_head(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    app = create_app(_settings(tmp_path, tmp_path))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-one",
                    "include_workspace_changes": True,
                },
            ),
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_repository"


def test_local_http_safety_rejects_form_cross_origin_and_untrusted_host(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    payload = _request(
        git_repository,
        {
            "type": "branch",
            "base_ref": "main",
            "target_ref": "feature-one",
            "include_workspace_changes": False,
        },
    )
    app = create_app(_settings(tmp_path, tmp_path))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        form = client.post("/api/reviews", data={"repository_path": str(git_repository)})
        untrusted_host = client.post(
            "/api/reviews",
            json=payload,
            headers={"Host": "attacker.example"},
        )
        userinfo_host = client.post(
            "/api/reviews",
            json=payload,
            headers={"Host": "attacker@127.0.0.1"},
        )
        cross_origin = client.post(
            "/api/reviews",
            json=payload,
            headers={"Origin": "https://attacker.example"},
        )
        userinfo_origin = client.post(
            "/api/reviews",
            json=payload,
            headers={"Origin": "https://attacker@127.0.0.1"},
        )

    assert form.status_code == 415
    assert untrusted_host.status_code == 400
    assert userinfo_host.status_code == 400
    assert cross_origin.status_code == 403
    assert userinfo_origin.status_code == 403


def test_review_query_cancel_report_and_sse_resume_contract(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    settings = _settings(tmp_path, tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        created = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-one",
                    "include_workspace_changes": False,
                },
            ),
        )
        task_id = created.json()["task_id"]
        queried = client.get(f"/api/reviews/{task_id}")
        canceled = client.post(f"/api/reviews/{task_id}/cancel", json={})
        canceled_again = client.post(f"/api/reviews/{task_id}/cancel", json={})
        report = client.get(f"/api/reviews/{task_id}/report")

        event_store = app.state.components.events
        initial_events = client.portal.call(
            partial(event_store.list_after, task_id, after_event_id=0)
        )
        created_event_id = initial_events[0].event_id
        client.portal.call(
            event_store.append,
            task_id,
            "review.completed",
            {"status": "completed", "finding_count": 0},
        )
        stream = client.get(
            f"/api/reviews/{task_id}/events",
            headers={"Last-Event-ID": str(created_event_id)},
        )
        invalid_event_id = client.get(
            f"/api/reviews/{task_id}/events",
            headers={"Last-Event-ID": "../../etc/passwd"},
        )

    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1:8765",
    ) as restarted_client:
        persisted = restarted_client.get(f"/api/reviews/{task_id}")

    assert created.status_code == 202
    assert queried.status_code == 200
    assert queried.json()["base_oid"] == created.json()["base_oid"]
    assert canceled.status_code == 202
    assert canceled.json()["cancellation_requested"] is True
    assert canceled_again.status_code == 202
    assert sum(
        event.event_type == "review.cancel_requested" for event in initial_events
    ) == 1
    assert report.status_code == 404
    assert report.json()["code"] == "report_not_ready"
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: review.cancel_requested" in stream.text
    assert "event: review.completed" in stream.text
    assert "event: review.created" not in stream.text
    assert str(tmp_path) not in stream.text
    assert invalid_event_id.status_code == 422
    assert persisted.status_code == 200
    assert persisted.json()["cancellation_requested"] is True


def test_review_findings_endpoint_returns_empty_then_saved_findings(
    tmp_path: Path,
) -> None:
    git_repository = tmp_path / "repo"
    git_repository.mkdir()
    _run_git_safe("init", "-b", "main", str(git_repository))
    _run_git_safe("-C", str(git_repository), "config", "user.email", "test@example.com")
    _run_git_safe("-C", str(git_repository), "config", "user.name", "Test User")
    (git_repository / "README.md").write_text("# fixture\n", encoding="utf-8")
    _run_git_safe("-C", str(git_repository), "add", "README.md")
    _run_git_safe("-C", str(git_repository), "commit", "-m", "initial")
    _run_git_safe("-C", str(git_repository), "switch", "-c", "feature-one")
    (git_repository / "feature.py").write_text("value = 1\n", encoding="utf-8")
    _run_git_safe("-C", str(git_repository), "add", "feature.py")
    _run_git_safe("-C", str(git_repository), "commit", "-m", "feature-one")
    _run_git_safe("-C", str(git_repository), "switch", "main")
    settings = _settings(tmp_path, tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        created = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-one",
                    "include_workspace_changes": False,
                },
            ),
        )
        task_id = created.json()["task_id"]
        empty = client.get(f"/api/reviews/{task_id}/findings")
        checkpoint_store = SqlCheckpointStore(app.state.components.database)
        finding = Finding(
            finding_id="finding_1",
            fingerprint="d" * 64,
            reviewer_id="correctness",
            category="branching",
            title="Wrong branch",
            severity=FindingSeverity.MEDIUM,
            disposition=FindingDisposition.NON_BLOCKING,
            confidence=0.91,
            primary_location=SourceLocation(
                path="feature.py",
                start_line=1,
                end_line=2,
                side="new",
                excerpt_hash="e" * 64,
                is_deleted=False,
            ),
            related_locations=(),
            changed_hunk_id=None,
            change_origin=ChangeOrigin.INTRODUCED,
            evidence=(
                Evidence(
                    kind="excerpt",
                    description="Captured from the saved review output.",
                    artifact_ref=None,
                    excerpt_hash="e" * 64,
                ),
            ),
            impact="The review pointed at the wrong branch.",
            explanation="This is a stored contract fixture.",
            reproduction=None,
            recommendation="Review the correct branch target.",
            suggested_patch=None,
            rule_sources=(RuleReference("rules/review.md", "f" * 64),),
        )
        batch = FindingBatch("1", (finding,))
        node_key = "correctness:v1:0:root"
        client.portal.call(checkpoint_store.ensure, task_id, node_key, "primary")
        client.portal.call(checkpoint_store.mark_running, task_id, node_key)
        client.portal.call(
            checkpoint_store.mark_output_saved,
            task_id,
            node_key,
            "artifact_1",
            "a" * 64,
        )
        client.portal.call(
            app.state.components.review_store.complete_with_findings,
            task_id,
            node_key,
            batch,
        )
        saved = client.get(f"/api/reviews/{task_id}/findings")

    assert empty.status_code == 200
    assert empty.json() == []
    assert saved.status_code == 200
    body = saved.json()
    assert len(body) == 1
    assert body[0]["title"] == "Wrong branch"
    assert body[0]["severity"] == "medium"


def test_reviews_are_listed_as_workspaces_and_can_be_soft_deleted(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        first = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-one",
                    "include_workspace_changes": False,
                },
            ),
        )
        second = client.post(
            "/api/reviews",
            json=_request(
                git_repository,
                {
                    "type": "branch",
                    "base_ref": "main",
                    "target_ref": "feature-two",
                    "include_workspace_changes": False,
                },
            ),
        )
        listed = client.get("/api/reviews")
        deleted = client.request(
            "DELETE",
            f"/api/reviews/{first.json()['task_id']}",
            json={},
        )
        after_delete = client.get("/api/reviews")
        hidden = client.get(f"/api/reviews/{first.json()['task_id']}")

    assert first.status_code == second.status_code == 202
    assert listed.status_code == 200, listed.text
    assert [review["task_id"] for review in listed.json()] == [
        second.json()["task_id"],
        first.json()["task_id"],
    ]
    assert all(review["repository_name"] == git_repository.name for review in listed.json())
    assert all(review["created_at"] for review in listed.json())
    assert deleted.status_code == 204, deleted.text
    assert [review["task_id"] for review in after_delete.json()] == [second.json()["task_id"]]
    assert hidden.status_code == 404

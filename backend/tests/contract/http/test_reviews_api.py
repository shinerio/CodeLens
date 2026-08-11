import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from codelens.bootstrap.settings import Settings
from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingDisposition,
    FindingSeverity,
    RuleReference,
    SourceLocation,
)
from codelens.interface.http.app import create_app
from codelens.review.domain.review_plan import (
    ReviewPass,
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)
from codelens.review.infrastructure.tables import findings, verdict_decisions
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
        "reviewer_selection": {
            "mode": "fixed",
            "reviewer_versions": ["correctness:v2"],
        },
    }


def test_create_review_rejects_removed_selected_agents_field(
    tmp_path: Path, git_repository: Path
) -> None:
    _prepared_repository(git_repository)
    payload = _request(git_repository, {"type": "uncommitted"})
    payload["selected_agents"] = ["correctness:v2"]

    with TestClient(
        create_app(_settings(tmp_path, tmp_path)),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = client.post("/api/reviews", json=payload)

    assert response.status_code == 422


def test_v2_adaptive_selection_is_persisted_without_legacy_upgrade(
    tmp_path: Path, git_repository: Path
) -> None:
    _prepared_repository(git_repository)
    payload = {
        "repository_path": str(git_repository),
        "scope": {
            "type": "branch",
            "base_ref": "main",
            "target_ref": "feature-one",
            "include_workspace_changes": False,
        },
        "reviewer_selection": {"mode": "adaptive"},
        "prompt_locale": "en",
    }

    with TestClient(
        create_app(_settings(tmp_path, tmp_path)),
        base_url="http://127.0.0.1:8765",
    ) as client:
        created = client.post("/api/reviews", json=payload)
        response = client.get(f"/api/reviews/{created.json()['task_id']}")

    assert created.status_code == 202
    assert response.status_code == 200
    assert response.json()["selection_request"] == {"mode": "adaptive"}
    assert response.json()["selected_agents"] == []
    assert response.json()["review_plan"] is None


def test_review_plan_projection_includes_derived_plan_hash(
    tmp_path: Path, git_repository: Path
) -> None:
    _prepared_repository(git_repository)
    app = create_app(_settings(tmp_path, tmp_path))

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
        reviewer = ReviewPlanNode.create(
            task_id=task_id,
            node_type=ReviewPlanNodeType.REVIEWER,
            agent_reference="correctness:v2",
            pass_index=ReviewPass.REVIEWER,
            shard_id="root",
            logical_attempt_group="primary",
            depends_on=(),
        )
        plan = ReviewPlan.create(
            task_id=task_id,
            selection_mode="fixed",
            reviewer_references=("correctness:v2",),
            nodes=(reviewer,),
            planner_reason=None,
        )
        client.portal.call(
            partial(
                app.state.components.review_plan_store.save,
                plan,
                catalog_version="test-catalog",
                capability_fingerprint="a" * 64,
            )
        )

        response = client.get(f"/api/reviews/{task_id}")

    assert response.status_code == 200
    assert response.json()["review_plan"]["plan_hash"] == plan.plan_hash


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


def test_recent_repositories_deduplicates_review_paths(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    settings = _settings(tmp_path, tmp_path)
    request = _request(
        git_repository,
        {
            "type": "branch",
            "base_ref": "main",
            "target_ref": "feature-one",
            "include_workspace_changes": False,
        },
    )

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        first = client.post("/api/reviews", json=request)
        second = client.post("/api/reviews", json=request)
        assert first.status_code == 202
        assert second.status_code == 202

        first_delete = client.request("DELETE", f"/api/reviews/{first.json()['task_id']}", json={})
        second_delete = client.request(
            "DELETE", f"/api/reviews/{second.json()['task_id']}", json={}
        )
        assert first_delete.status_code == 204
        assert second_delete.status_code == 204

        response = client.get("/api/repositories/recent")

    assert response.status_code == 200
    assert response.json() == [
        {
            "repository_name": git_repository.name,
            "repository_path": str(git_repository.resolve()),
            "last_reviewed_at": response.json()[0]["last_reviewed_at"],
        }
    ]
    assert response.json()[0]["last_reviewed_at"].endswith(("Z", "+00:00"))


def test_retry_failed_review_creates_a_new_review_from_the_original_request(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    app = create_app(_settings(tmp_path, tmp_path))
    request = _request(
        git_repository,
        {
            "type": "branch",
            "base_ref": "main",
            "target_ref": "feature-one",
            "include_workspace_changes": False,
        },
    )

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        original = client.post("/api/reviews", json=request)
        original_task_id = original.json()["task_id"]
        client.portal.call(
            app.state.components.review_store.fail,
            original_task_id,
            "review_execution_failed",
        )

        retried = client.post(f"/api/reviews/{original_task_id}/retry", json={})
        original_after_retry = client.get(f"/api/reviews/{original_task_id}")

    assert retried.status_code == 202, retried.text
    assert retried.json()["task_id"] != original_task_id
    assert retried.json()["status"] == "created"
    assert retried.json()["base_oid"] == original.json()["base_oid"]
    assert retried.json()["head_oid"] == original.json()["head_oid"]
    assert retried.json()["selected_agents"] == original.json()["selected_agents"]
    assert original_after_retry.json()["status"] == "failed"


def test_retry_rejects_a_review_that_is_not_failed(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    app = create_app(_settings(tmp_path, tmp_path))

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        original = client.post(
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
        response = client.post(f"/api/reviews/{original.json()['task_id']}/retry", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_agent_run_state"


def test_recent_repository_can_be_removed_without_deleting_reviews(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    settings = _settings(tmp_path, tmp_path)
    request = _request(
        git_repository,
        {
            "type": "branch",
            "base_ref": "main",
            "target_ref": "feature-one",
            "include_workspace_changes": False,
        },
    )

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        created = client.post("/api/reviews", json=request)
        removed = client.request(
            "DELETE",
            "/api/repositories/recent",
            json={"repository_path": str(git_repository.resolve())},
        )
        removed_again = client.request(
            "DELETE",
            "/api/repositories/recent",
            json={"repository_path": str(git_repository.resolve())},
        )
        recent = client.get("/api/repositories/recent")
        reviews = client.get("/api/reviews")

    assert created.status_code == 202
    assert removed.status_code == 204
    assert removed_again.status_code == 204
    assert recent.json() == []
    assert [review["task_id"] for review in reviews.json()] == [created.json()["task_id"]]
    assert reviews.json()[0]["created_at"].endswith(("Z", "+00:00"))


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
    assert body["selected_agents"] == ["correctness:v2"]
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
    assert sum(event.event_type == "review.cancel_requested" for event in initial_events) == 1
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


def test_sse_replay_skips_stale_intermediate_terminal_events(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    """When a task has multiple terminal events (e.g. partial→failed→completed
    after recovery), SSE replay must only send the LAST terminal event so the
    frontend observes the final status, not a stale intermediate one."""

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

        event_store = app.state.components.events
        # Simulate a recovery scenario: partial → failed → completed
        client.portal.call(
            event_store.append,
            task_id,
            "review.partial",
            {"status": "partial"},
        )
        client.portal.call(
            event_store.append,
            task_id,
            "review.failed",
            {"status": "failed", "error_code": "review_execution_failed"},
        )
        client.portal.call(
            event_store.append,
            task_id,
            "review.completed",
            {"status": "completed", "finding_count": 2},
        )

        stream = client.get(f"/api/reviews/{task_id}/events")

    assert stream.status_code == 200
    assert "event: review.partial" not in stream.text
    assert "event: review.failed" not in stream.text
    assert "event: review.completed" in stream.text


def test_review_findings_endpoint_returns_empty_then_saved_findings(
    tmp_path: Path,
) -> None:
    git_repository = tmp_path / "repo"
    git_repository.mkdir()
    _run_git_safe("init", "-b", "main", str(git_repository))
    _run_git_safe("-C", str(git_repository), "config", "user.email", "test@example.com")
    _run_git_safe("-C", str(git_repository), "config", "user.name", "Test User")
    _run_git_safe("-C", str(git_repository), "config", "commit.gpgSign", "false")
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
            rule_sources=(RuleReference("rules/review.md", "f" * 64),),
        )

        async def persist_verdict_finding() -> None:
            async def operation(session: AsyncSession) -> None:
                await session.execute(
                    insert(verdict_decisions).values(
                        verdict_decision_id="decision-1",
                        task_id=task_id,
                        verifier_run_id="verifier-run-1",
                        outcome="merge",
                        payload_json="{}",
                        created_at=datetime.now(UTC),
                    )
                )
                await session.execute(
                    insert(findings).values(
                        finding_id=finding.finding_id,
                        task_id=task_id,
                        node_key="review-verifier:v2:0:batch",
                        fingerprint=finding.fingerprint,
                        payload_json=json.dumps(asdict(finding), default=str),
                        severity=finding.severity.value,
                        verdict_decision_id="decision-1",
                        verification_status="confirmed",
                        path=finding.primary_location.path,
                        start_line=finding.primary_location.start_line,
                        created_at=datetime.now(UTC),
                    )
                )

            await app.state.components.database.run_transaction(operation)

        client.portal.call(persist_verdict_finding)
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


def test_review_transcript_contract_returns_empty_history_before_worker_execution(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    app = create_app(Settings(data_dir=tmp_path / "data"))

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
        transcript = client.get(f"/api/reviews/{created.json()['task_id']}/transcript")

    assert created.status_code == 202, created.text
    assert transcript.status_code == 200, transcript.text
    assert transcript.json() == []


def test_terminal_review_process_report_returns_usage_and_tool_totals(
    tmp_path: Path,
    git_repository: Path,
) -> None:
    _prepared_repository(git_repository)
    app = create_app(_settings(tmp_path, tmp_path))

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
        active_report = client.get(f"/api/reviews/{task_id}/process-report")
        review_store = app.state.components.review_store
        for status in (
            "provisioning_worktree",
            "snapshotting",
            "preparing",
            "reviewing",
            "validating",
            "synthesizing",
            "completed",
        ):
            client.portal.call(review_store.transition, task_id, status)
        pending_persistence_report = client.get(f"/api/reviews/{task_id}/process-report")
        transcripts = app.state.components.transcripts
        client.portal.call(
            transcripts.append_many,
            task_id,
            (
                ("model_started", "", {"agent": "correctness:v2"}),
                (
                    "tool_call",
                    "{}",
                    {
                        "agent": "correctness:v2",
                        "tool_name": "read_file",
                        "tool_call_id": "call-1",
                    },
                ),
                (
                    "tool_result",
                    "{}",
                    {"agent": "correctness:v2", "tool_call_id": "call-1"},
                ),
                (
                    "model_output",
                    "{}",
                    {
                        "agent": "correctness:v2",
                        "model_name": "gpt-5.1",
                        "llm_call_count": "2",
                        "input_tokens": "80",
                        "cached_input_tokens": "30",
                        "cache_write_input_tokens": "10",
                        "context_compaction_count": "1",
                        "context_compacted_result_count": "3",
                        "context_compaction_original_bytes": "9000",
                        "context_compaction_compressed_bytes": "600",
                        "output_tokens": "20",
                        "total_tokens": "100",
                    },
                ),
                (
                    "invalid_tool_call",
                    "{}",
                    {
                        "agent": "correctness:v2",
                        "tool_name": "grep_create_triggered",
                    },
                ),
            ),
        )
        report = client.get(f"/api/reviews/{task_id}/process-report")

    assert active_report.status_code == 409
    assert active_report.json()["code"] == "process_report_not_ready"
    assert pending_persistence_report.status_code == 409
    assert pending_persistence_report.json()["code"] == "process_report_not_ready"
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["task_id"] == task_id
    assert body["status"] == "completed"
    assert body["llm_call_count"] == 2
    assert body["cached_input_tokens"] == 30
    assert body["cache_write_input_tokens"] == 10
    assert body["context_compaction_count"] == 1
    assert body["context_compacted_result_count"] == 3
    assert body["context_compaction_original_bytes"] == 9000
    assert body["context_compaction_compressed_bytes"] == 600
    assert body["total_tokens"] == 100
    assert body["tool_call_count"] == 1
    assert body["invalid_tool_call_count"] == 1
    assert body["tools"] == [{"tool_name": "read_file", "call_count": 1, "result_count": 1}]
    assert body["invalid_tools"] == [
        {"tool_name": "grep_create_triggered", "call_count": 1}
    ]
    assert body["usage_is_complete"] is True

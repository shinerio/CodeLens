import asyncio
import json
import socket
import stat
from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(Settings(data_dir=tmp_path / "data")),
        base_url="http://127.0.0.1:8765",
    )


def test_multiple_model_gateways_are_redacted_switchable_and_persistent(
    tmp_path: Path,
) -> None:
    primary_key = "sk-primary-test-secret"
    secondary_key = "sk-secondary-test-secret"

    with _client(tmp_path) as client:
        empty = client.get("/api/settings/model-gateways")
        primary = client.post(
            "/api/settings/model-gateways",
            json={
                "name": "Primary gateway",
                "api_key": primary_key,
                "model": "gpt-primary",
                "base_url": "https://primary.example/v1",
            },
        )
        primary_id = primary.json()["active_gateway_id"]
        secondary = client.post(
            "/api/settings/model-gateways",
            json={
                "name": "Secondary gateway",
                "api_key": secondary_key,
                "model": "gpt-secondary",
                "base_url": "http://secondary.example:8080",
            },
        )
        secondary_id = next(
            gateway["gateway_id"]
            for gateway in secondary.json()["gateways"]
            if gateway["name"] == "Secondary gateway"
        )
        activated = client.put(
            "/api/settings/active-model-gateway",
            json={"gateway_id": secondary_id},
        )
        updated = client.put(
            f"/api/settings/model-gateways/{primary_id}",
            json={
                "name": "Primary renamed",
                "model": "gpt-primary-2",
                "base_url": "https://primary.example/v2",
            },
        )

    assert empty.status_code == 200
    assert empty.json() == {"active_gateway_id": None, "gateways": []}
    assert primary.status_code == 201, primary.text
    assert primary_id.startswith("gateway_")
    assert secondary.status_code == 201, secondary.text
    assert secondary.json()["active_gateway_id"] == primary_id
    assert activated.status_code == 200, activated.text
    assert activated.json()["active_gateway_id"] == secondary_id
    assert updated.status_code == 200, updated.text
    assert updated.json()["active_gateway_id"] == secondary_id
    assert all("api_key" not in gateway for gateway in updated.json()["gateways"])
    assert primary_key not in updated.text
    assert secondary_key not in updated.text

    secret_file = tmp_path / "data" / "secrets" / "model-gateways.json"
    payload = json.loads(secret_file.read_text(encoding="utf-8"))
    assert {gateway["api_key"] for gateway in payload["gateways"]} == {
        primary_key,
        secondary_key,
    }
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600

    with _client(tmp_path) as restarted_client:
        persisted = restarted_client.get("/api/settings/model-gateways")
        legacy_active = restarted_client.get("/api/settings/openai")
        deleted = restarted_client.request(
            "DELETE",
            f"/api/settings/model-gateways/{secondary_id}",
            json={},
        )

    assert persisted.status_code == 200
    assert persisted.json()["active_gateway_id"] == secondary_id
    assert len(persisted.json()["gateways"]) == 2
    assert legacy_active.json() == {
        "is_configured": True,
        "model": "gpt-secondary",
        "base_url": "http://secondary.example:8080",
    }
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["active_gateway_id"] == primary_id
    assert [gateway["name"] for gateway in deleted.json()["gateways"]] == [
        "Primary renamed"
    ]


def test_model_gateway_update_rejects_unknown_gateway_without_leaking_key(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        response = client.put(
            "/api/settings/model-gateways/gateway_00000000000000000000000000000000",
            json={
                "name": "Missing",
                "api_key": "sk-missing-test-secret",
                "model": "gpt-test",
                "base_url": "https://missing.example/v1",
            },
        )

    assert response.status_code == 404
    assert "sk-missing-test-secret" not in response.text


def _free_tcp_port() -> int:
    """Reserve and immediately release a TCP port for test scaffolding."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_gateway_connectivity_succeeds_for_reachable_host(tmp_path: Path) -> None:
    async def _noop_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.close()

    async def _serve() -> tuple[asyncio.base_events.Server, int]:
        server = await asyncio.start_server(_noop_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return server, port

    loop = asyncio.new_event_loop()
    server, port = loop.run_until_complete(_serve())

    with _client(tmp_path) as client:
        created = client.post(
            "/api/settings/model-gateways",
            json={
                "name": "Reachable",
                "api_key": "sk-reachable-test",
                "model": "gpt-test",
                "base_url": f"http://127.0.0.1:{port}/v1",
            },
        )
        gateway_id = created.json()["active_gateway_id"]
        result = client.post(
            f"/api/settings/model-gateways/{gateway_id}/test-connectivity",
            json={},
        )

    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is True
    assert body["latency_ms"] is not None
    assert "sk-reachable-test" not in result.text


def test_gateway_connectivity_fails_for_closed_port(tmp_path: Path) -> None:
    closed_port = _free_tcp_port()

    with _client(tmp_path) as client:
        created = client.post(
            "/api/settings/model-gateways",
            json={
                "name": "Unreachable",
                "api_key": "sk-unreachable-test",
                "model": "gpt-test",
                "base_url": f"http://127.0.0.1:{closed_port}/v1",
            },
        )
        gateway_id = created.json()["active_gateway_id"]
        result = client.post(
            f"/api/settings/model-gateways/{gateway_id}/test-connectivity",
            json={},
        )

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is False
    assert body["detail"]


def test_gateway_connectivity_returns_404_for_missing_gateway(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        result = client.post(
            "/api/settings/model-gateways/gateway_00000000000000000000000000000000/test-connectivity",
            json={},
        )

    assert result.status_code == 404


def test_gateway_availability_returns_404_for_missing_gateway(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        result = client.post(
            "/api/settings/model-gateways/gateway_00000000000000000000000000000000/test-availability",
            json={},
        )

    assert result.status_code == 404


def test_gateway_availability_fails_for_non_llm_endpoint(tmp_path: Path) -> None:
    closed_port = _free_tcp_port()

    with _client(tmp_path) as client:
        created = client.post(
            "/api/settings/model-gateways",
            json={
                "name": "Non-LLM",
                "api_key": "sk-non-llm-test",
                "model": "gpt-test",
                "base_url": f"http://127.0.0.1:{closed_port}/v1",
            },
        )
        gateway_id = created.json()["active_gateway_id"]
        result = client.post(
            f"/api/settings/model-gateways/{gateway_id}/test-availability",
            json={},
        )

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is False
    assert body["detail"]
    assert "sk-non-llm-test" not in result.text

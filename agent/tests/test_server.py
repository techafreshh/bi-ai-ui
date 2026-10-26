"""Black-box contract tests for the walking skeleton (no OpenRouter calls)."""

import importlib

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.app)


def test_health_returns_exact_contract(client: TestClient) -> None:
    """GET /health -> 200 with body exactly {"status": "ok"}."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_listen_port_honors_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server binds $PORT when the environment assigns one; else 8123."""
    monkeypatch.setenv("PORT", "8120")
    assert server._listen_port() == 8120
    monkeypatch.delenv("PORT", raising=False)
    assert server._listen_port() == 8123


def test_cors_preflight_allows_spa_origin(client: TestClient) -> None:
    """Preflight from the SPA origin against /copilotkit is allowed."""
    r = client.options(
        "/copilotkit",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_missing_api_key_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without OPENROUTER_API_KEY the graph's chat node raises a clear error."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    graph = importlib.import_module("graph")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set"):
        graph._build_model()


def test_run_round_trip_streams_text_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full AG-UI round-trip with a fake model: RUN_STARTED -> TEXT_MESSAGE_*
    -> RUN_FINISHED (the same wire the browser HttpAgent consumes)."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    graph = importlib.import_module("graph")
    fake_model = GenericFakeChatModel(messages=iter([AIMessage(content="pong reply")]))
    monkeypatch.setattr(graph, "_build_model", lambda: fake_model)

    payload = {
        "threadId": "t1",
        "runId": "r1",
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "messages": [{"id": "m1", "role": "user", "content": "ping"}],
    }
    with client.stream(
        "POST",
        "/copilotkit",
        json=payload,
        headers={"Origin": "http://localhost:5173", "Accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert '"type":"RUN_STARTED"' in body
    assert '"type":"TEXT_MESSAGE_CONTENT"' in body
    assert "pong" in body
    assert '"type":"RUN_FINISHED"' in body

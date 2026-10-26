"""HTTP-contract tests for the agent (issue #1 acceptance scenarios)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import graph as agent_graph
from server import app

client = TestClient(app)


def test_health_returns_exact_contract():
    """GET /health must be 200 with exactly {"status": "ok"}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_prefflight_allows_spa_origin():
    """Preflight from the Vite dev origin must be allowed on /copilotkit."""
    response = client.options(
        "/copilotkit",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_no_key_still_boots_and_serves_health(monkeypatch):
    """No OPENROUTER_API_KEY in env: app import and /health still work.

    Module-level server.py logs a clear error naming the variable at boot;
    graph.build_chat_model only raises when a chat request needs the model.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import time

    with TestClient(app) as boot_client:
        t0 = time.monotonic()
        response = boot_client.get("/health")
        elapsed = time.monotonic() - t0
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}
    assert elapsed < 2.0, f"/health took {elapsed:.2f}s with no key set"


def test_unknown_top_level_path_answers_404_fast():
    """Holdout contract: an unknown top-level path answers 404 — never 5xx,
    never hang. Probed with several methods/shapes and timed against the
    verification gate's 5s budget.
    """
    import time

    probes = [
        ("GET", "/does-not-exist"),
        ("GET", "/definitely-not-a-route/"),
        ("POST", "/nope"),
        ("HEAD", "/missing"),
    ]
    for method, path in probes:
        t0 = time.monotonic()
        response = client.request(method, path)
        elapsed = time.monotonic() - t0
        assert response.status_code == 404, f"{method} {path}: {response.status_code}"
        assert response.status_code < 500, f"{method} {path}: must not be 5xx"
        assert elapsed < 2.0, f"{method} {path} took {elapsed:.2f}s (gate budget 5s)"


def test_chat_node_raises_clear_error_without_api_key(monkeypatch):
    """No OPENROUTER_API_KEY: /health still serves, chat fails loudly."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        asyncio.run(agent_graph.chat({"messages": []}))

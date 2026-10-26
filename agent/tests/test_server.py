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


def test_chat_node_raises_clear_error_without_api_key(monkeypatch):
    """No OPENROUTER_API_KEY: /health still serves, chat fails loudly."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        asyncio.run(agent_graph.chat({"messages": []}))

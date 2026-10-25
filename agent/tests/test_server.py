"""Black-box contract tests for the FastAPI server (no OpenRouter access needed)."""

import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient

import graph
import server


def test_health_returns_exact_contract():
    client = TestClient(server.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_cors_preflight_allows_spa_origin():
    client = TestClient(server.app)
    resp = client.options(
        "/copilotkit",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_missing_api_key_raises_clear_error_from_graph():
    env_without_key = {
        k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"
    }
    graph.reset_text_model()
    with mock.patch.dict(os.environ, env_without_key, clear=True):
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            graph.get_text_model()
    graph.reset_text_model()


def test_server_reports_missing_key_and_still_serves_health(capsys):
    env_without_key = {
        k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"
    }
    with mock.patch.dict(os.environ, env_without_key, clear=True):
        app = server.create_app()
        assert TestClient(app).get("/health").json() == {"status": "ok"}
    graph.reset_text_model()

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


def test_chat_without_key_returns_error_message_not_exception():
    import asyncio

    from langchain_core.messages import HumanMessage

    env_without_key = {
        k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"
    }
    graph.reset_text_model()
    with mock.patch.dict(os.environ, env_without_key, clear=True):
        result = asyncio.run(graph.chat({"messages": [HumanMessage(content="ping")]}))
    graph.reset_text_model()
    reply = result["messages"][0]
    assert "OPENROUTER_API_KEY" in reply.content
    assert reply.content.startswith("⚠")


def test_chat_merges_streamed_chunks_into_one_message():
    import asyncio

    from langchain_core.messages import AIMessageChunk, HumanMessage

    class FakeModel:
        def __init__(self, chunks):
            self._chunks = chunks

        async def astream(self, _messages):
            for c in self._chunks:
                yield c

    fake = FakeModel([AIMessageChunk(content="he"), AIMessageChunk(content="llo")])
    with mock.patch.object(graph, "get_text_model", return_value=fake):
        result = asyncio.run(graph.chat({"messages": [HumanMessage(content="ping")]}))
    assert result["messages"][0].content == "hello"


def test_server_reports_missing_key_and_still_serves_health(capsys):
    env_without_key = {
        k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"
    }
    with mock.patch.dict(os.environ, env_without_key, clear=True):
        app = server.create_app()
        assert TestClient(app).get("/health").json() == {"status": "ok"}
    graph.reset_text_model()

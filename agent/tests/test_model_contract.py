"""Live contract tests against a fake OpenAI-compatible streaming server.

Proves the whole round-trip path issue #1's chat scenario depends on —
RunAgentInput → CorSMiddleware → /copilotkit → LangGraph node →
ChatOpenAI streaming client → SSE TEXT_MESSAGE deltas → RUN_FINISHED —
without needing a real OPENROUTER_API_KEY. The fake server records the
request so the OpenRouter-shaped contract (bearer key, model id, chat
path) is asserted too.
"""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.fake_openai_server import FakeOpenAICompatServer

REPLY = "pong"

RUN_BODY = {
    "threadId": "ctest-thread",
    "runId": "ctest-run",
    "state": {},
    "tools": [],
    "context": [],
    "forwardedProps": {},
    "messages": [{"id": "m1", "role": "user", "content": "ping"}],
}


@pytest.fixture()
def fake_provider(monkeypatch) -> Iterator[FakeOpenAICompatServer]:
    with FakeOpenAICompatServer(reply=REPLY) as server:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("OPENROUTER_BASE_URL", server.base_url)
        monkeypatch.delenv("TEXT_MODEL", raising=False)  # default model id
        import graph as agent_graph

        monkeypatch.setattr(agent_graph, "OPENROUTER_BASE_URL", server.base_url)
        yield server


def sse_lines(client: TestClient) -> Iterator[str]:
    with client.stream(
        "POST",
        "/copilotkit",
        json=RUN_BODY,
        headers={
            "Accept": "text/event-stream",
            "Origin": "http://localhost:5173",
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            yield line


def payload_lines(lines: Iterator[str]) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in lines
        if line.startswith("data: ")
    ]


def test_chat_node_streams_from_openai_compatible_provider(fake_provider):
    """The graph node's merged reply equals the provider's streamed text."""
    import asyncio

    from langchain_core.messages import AIMessage

    import graph as agent_graph

    state = {"messages": [("user", "ping")]}
    result = asyncio.run(agent_graph.chat(state))
    merged = result["messages"][-1]
    assert merged.content == REPLY
    assert isinstance(merged, AIMessage)

    request = fake_provider.requests[0]
    assert request["path"].endswith("/v1/chat/completions")
    assert request["authorization"] == "Bearer test-key"
    assert request["body"]["model"] == "openai/gpt-6-luna"


def test_copilotkit_endpoint_streams_text_reply(fake_provider):
    """POST /copilotkit runs the graph and streams TEXT_MESSAGE_* deltas."""
    client = TestClient(__import__("server").app)
    payloads = payload_lines(sse_lines(client))

    types = [p["type"] for p in payloads]
    assert "RUN_STARTED" in types
    assert types.count("TEXT_MESSAGE_CONTENT") >= 1
    assert "RUN_FINISHED" in types
    assert "RUN_ERROR" not in types

    stream = "".join(
        p["delta"] for p in payloads if p["type"] == "TEXT_MESSAGE_CONTENT"
    )
    assert stream == REPLY

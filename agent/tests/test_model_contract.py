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

import datasets
from tests.fake_openai_server import FakeOpenAICompatServer

REPLY = "pong"

@pytest.fixture(scope="module", autouse=True)
def _active_seed(seeded_db):
    """Issue #85 rescoped the graph to the single-active dataset: activate
    the module seed so /copilotkit runs read it (the empty short-circuit
    makes no text-model call, which this contract forbids); save/restore
    keeps the id out of later modules."""
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets.register("seeded", seeded_db, "sample")
    assert datasets.set_active("seeded")
    try:
        yield
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active


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
        # server.py load_dotenv() may repopulate a local DECISION_ENDPOINT;
        # pin it to the fake so no test ever reaches the live alpha API.
        monkeypatch.setenv(
            "DECISION_ENDPOINT", f"{server.base_url}/api/alpha/decisions"
        )
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


def test_fill_calls_openai_compatible_provider(fake_provider):
    """The graph's fill node makes its one text-model call through the same
    OpenAI-compatible contract (issue #10 replaced the single chat node;
    the provider-facing request shape is unchanged)."""
    client = TestClient(__import__("server").app)
    payloads = payload_lines(sse_lines(client))

    types = [p["type"] for p in payloads]
    assert "RUN_FINISHED" in types
    assert "RUN_ERROR" not in types
    snapshots = [p["snapshot"] for p in payloads if p["type"] == "STATE_SNAPSHOT"]
    assert snapshots, "the graph must stream shared state"
    # The unparseable (non-JSON) reply streams as the chat's summary
    # verbatim — the streamed AIMessage the run closes on.
    final_state = snapshots[-1]
    final_messages = final_state["messages"]
    assert final_messages[-1]["type"] == "ai"
    assert final_messages[-1]["content"] == REPLY
    final_a2ui = final_state["a2ui"]
    assert final_a2ui["slotCopy"]["summary"] == REPLY

    # The recorded request keeps the OpenRouter-shaped contract: bearer
    # key, default model id, chat-completions path. Decide ran first (its
    # decisions probe 404s on this fake and fell back), so fill's call is
    # the LAST chat-completions request the fake recorded.
    chat_requests = [
        request for request in fake_provider.requests
        if request["path"].endswith("/v1/chat/completions")
    ]
    assert chat_requests, "fill must call the text provider"
    fill_request = chat_requests[-1]
    assert fill_request["authorization"] == "Bearer test-key"
    assert fill_request["body"]["model"] == "openai/gpt-6-luna"


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

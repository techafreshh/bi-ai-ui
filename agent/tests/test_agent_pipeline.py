"""Pipeline test: RunAgentInput → LangGraphAGUIAgent → AG-UI SSE events.

Drives the real adapter (the code path the browser's HttpAgent POST hits)
against the real graph, with the OpenRouter model faked — proving the SSE
wire contract (RUN_STARTED → TEXT_MESSAGE_* → RUN_FINISHED) without a key.
"""

import pytest
from ag_ui.core.types import RunAgentInput, UserMessage
from ag_ui.encoder import EventEncoder
from copilotkit import LangGraphAGUIAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import graph as agent_graph

AGENT_NAME = "bi_agent"

FAKE_REPLY = "pong"


@pytest.fixture()
def sse_agent(monkeypatch) -> LangGraphAGUIAgent:
    """The real AG-UI agent over the real graph, model swapped for a fake."""
    monkeypatch.setattr(
        agent_graph, "build_chat_model", lambda: FakeListChatModel(responses=[FAKE_REPLY])
    )
    return LangGraphAGUIAgent(
        name=AGENT_NAME,
        description="pipeline test agent",
        graph=agent_graph.graph,
    )


def make_run_input() -> RunAgentInput:
    return RunAgentInput(
        thread_id="test-thread",
        run_id="test-run",
        state={},
        messages=[UserMessage(id="m1", role="user", content="ping")],
        tools=[],
        context=[],
        forwarded_props={},
    )


def encode_events(agent: LangGraphAGUIAgent, run_input: RunAgentInput) -> str:
    """Run the pipeline and return the concatenation of encoded SSE events."""
    encoder = EventEncoder(accept="text/event-stream")

    async def collect() -> str:
        chunks = [encoder.encode(event) async for event in agent.run(run_input)]
        return "".join(chunks)

    import asyncio

    return asyncio.run(collect())


def test_sse_stream_round_trip(sse_agent):
    """A user message produces a streamed text reply on the AG-UI wire."""
    raw = encode_events(sse_agent, make_run_input())

    assert "RUN_STARTED" in raw
    assert "RUN_FINISHED" in raw
    # The reply is streamed as TEXT_MESSAGE_* deltas, not one blob.
    assert raw.count("TEXT_MESSAGE_CONTENT") > 1
    # Multi-char fake response streams one char per chunk.
    assert "TEXT_MESSAGE_START" in raw
    assert "TEXT_MESSAGE_END" in raw


def test_sse_stream_replies_to_the_message(sse_agent):
    """The streamed content is the model's actual reply, in order."""
    raw = encode_events(sse_agent, make_run_input())

    deltas = [
        line.removeprefix("data: ")
        for line in raw.splitlines()
        if line.startswith("data: ")
            and '"delta"' in line
            and "TEXT_MESSAGE_CONTENT" in line
    ]
    assert deltas, "expected TEXT_MESSAGE_CONTENT deltas on the SSE stream"
    stream = "".join(
        __import__("json").loads(d)["delta"] for d in deltas
    )
    assert FAKE_REPLY.startswith(stream) or stream == FAKE_REPLY
    assert len(stream) == len(FAKE_REPLY)

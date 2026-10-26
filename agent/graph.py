"""Trivial single-node LangGraph used for the walking-skeleton chat round-trip.

The chat node streams tokens from the OpenRouter text model; streaming inside
the node is what produces TEXT_MESSAGE_* deltas on the AG-UI wire. The model is
built lazily so a missing OPENROUTER_API_KEY can never break import or /health.
"""

import os

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph, START

# Under uvicorn + ag-ui-langgraph the graph needs an explicit checkpointer.
_checkpointer = MemorySaver()

SYSTEM_PROMPT = (
    "You are the bi-ai-ui assistant, a concise business-analytics copilot."
)


def _build_model() -> ChatOpenAI:
    """Build the OpenRouter-backed chat model (raised lazily on first use)."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set (agent/.env). Chat cannot reach the "
            "OpenRouter model until a key is provided; /health keeps serving."
        )
    return ChatOpenAI(
        model=os.environ.get("TEXT_MODEL", "openai/gpt-6-luna"),
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        streaming=True,
    )


async def chat(state: MessagesState) -> dict:
    """Stream the model response and return the merged assistant message."""
    model = _build_model()
    text_parts: list[str] = []
    async for chunk in model.astream(
        [{"role": "system", "content": SYSTEM_PROMPT}, *state["messages"]]
    ):
        if isinstance(chunk, AIMessage) and chunk.content:
            text_parts.append(str(chunk.content))
    return {"messages": [AIMessage(content="".join(text_parts))]}


def build_graph():
    builder = StateGraph(MessagesState)
    builder.add_node("chat", chat)
    builder.add_edge(START, "chat")
    builder.add_edge("chat", END)
    return builder.compile(checkpointer=_checkpointer)


graph = build_graph()

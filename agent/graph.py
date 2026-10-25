"""Single-node chat graph for the walking skeleton.

One ``chat`` node calls the OpenRouter text model through the OpenAI-compatible
API and returns the merged reply. Token streaming inside the node is what
ag-ui-langgraph relays to the wire as AG-UI ``TEXT_MESSAGE_*`` deltas
(it runs the graph under ``astream_events`` and forwards
``on_chat_model_stream`` chunks).
"""

import logging
import os
from functools import reduce

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TEXT_MODEL = "openai/gpt-6-luna"

SYSTEM_PROMPT = (
    "You are the bi-ai-ui assistant, a business-intelligence chat agent. "
    "Answer in concise plain text."
)


class MissingKeyError(RuntimeError):
    """OPENROUTER_API_KEY is absent; chat cannot run (server still boots)."""


# Built lazily on first chat call so a missing key can never break module
# import or the /health endpoint.
_chat_model: ChatOpenAI | None = None


def get_text_model() -> ChatOpenAI:
    """Return the OpenRouter-backed chat model, building it on first use.

    Raises:
        MissingKeyError: if OPENROUTER_API_KEY is not set.
    """
    global _chat_model
    if _chat_model is not None:
        return _chat_model
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise MissingKeyError(
            "OPENROUTER_API_KEY is not set (agent/.env). Copy .env.example to "
            "agent/.env, add a key, and restart the agent."
        )
    _chat_model = ChatOpenAI(
        model=os.getenv("TEXT_MODEL", DEFAULT_TEXT_MODEL),
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        streaming=True,
    )
    return _chat_model


def reset_text_model() -> None:
    """Drop the cached model so the next call rebuilds it (used by tests)."""
    global _chat_model
    _chat_model = None


async def chat(state: MessagesState) -> dict:
    """Stream one model reply for the conversation so far.

    A missing API key becomes the assistant reply rather than a raised error:
    under ag-ui-langgraph a node exception aborts the SSE stream with no
    terminal RUN_ERROR event, which would leave the chat UI hanging. The
    startup log already reports the problem server-side (see server.py).
    """
    try:
        model = get_text_model()
    except MissingKeyError as exc:
        logger.warning("chat skipped: %s", exc)
        return {"messages": [AIMessage(content=f"⚠️ {exc}")]}
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    chunks = [chunk async for chunk in model.astream(messages)]
    if not chunks:
        return {"messages": [AIMessage(content="")]}
    # AIMessageChunk concatenates; the merged chunk is the final assistant
    # message for the state.
    return {"messages": [reduce(lambda a, b: a + b, chunks)]}


def build_graph() -> CompiledStateGraph:
    """Compile the single-node chat graph.

    The explicit MemorySaver is required: under uvicorn + ag-ui-langgraph
    there is no langgraph-cli checkpointer supplying one, and AG-UI runs carry
    a thread_id that LangGraph refuses without a checkpointer.
    """
    builder = StateGraph(MessagesState)
    builder.add_node("chat", chat)
    builder.add_edge(START, "chat")
    builder.add_edge("chat", END)
    return builder.compile(checkpointer=MemorySaver())

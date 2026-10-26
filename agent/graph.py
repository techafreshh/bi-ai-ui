"""The bi-ai-ui LangGraph agent: one chat node, streaming to OpenRouter.

Deliberately trivial for the walking skeleton (issue #1): a single `chat`
node whose only job is to prove the round-trip SPA → CopilotKit → agent →
OpenRouter → streamed text. Streaming the model *inside* the node is what
puts token deltas on the wire: the ag-ui-langgraph adapter consumes
`astream_events(v2)` and turns every `on_chat_model_stream` chunk into an
AG-UI TEXT_MESSAGE_CONTENT event.
"""

import os

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TEXT_MODEL = "openai/gpt-6-luna"
_NO_KEY_MESSAGE = (
    "OPENROUTER_API_KEY is not set. Copy .env.example to agent/.env, add your "
    "OpenRouter key, and restart the agent."
)

# One system prompt for the skeleton; persona copy comes with later phases.
SYSTEM_PROMPT = (
    "You are the bi-ai-ui agent, a concise assistant for a business "
    "intelligence dashboard. Answer briefly in plain text."
)


def build_chat_model() -> ChatOpenAI:
    """Build the OpenRouter-backed chat model lazily.

    Lazy so a missing key can never break module import or /health: the
    failure surfaces only when a chat request actually needs the model.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(_NO_KEY_MESSAGE)
    return ChatOpenAI(
        model=os.getenv("TEXT_MODEL", DEFAULT_TEXT_MODEL),
        api_key=api_key,
        # Default is OpenRouter; overridable so tests can point at a fake
        # OpenAI-compatible server and verify the whole streaming path.
        base_url=os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
        streaming=True,
    )


async def chat(state: MessagesState) -> dict:
    """Stream the model and return the merged AIMessage."""
    model = build_chat_model()
    merged: AIMessage | None = None
    async for chunk in model.astream(
        [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    ):
        merged = chunk if merged is None else merged + chunk
    return {"messages": [merged or AIMessage(content="")]}


builder = StateGraph(MessagesState)
builder.add_node("chat", chat)
builder.add_edge(START, "chat")
builder.add_edge("chat", END)

# FastAPI-specific (mirrors the official langgraph-fastapi starter):
# `langgraph dev` supplies its own checkpointer; under uvicorn +
# ag-ui-langgraph the graph needs an explicit MemorySaver for thread
# persistence within a process.
graph = builder.compile(checkpointer=MemorySaver())

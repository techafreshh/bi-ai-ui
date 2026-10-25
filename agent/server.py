"""FastAPI server exposing the bi-ai-ui agent over the AG-UI protocol at
``POST /copilotkit`` (CopilotKit's AG-UI endpoint) plus a strict ``GET /health``
contract of exactly ``{"status": "ok"}``.

Server shape mirrors the official CopilotKit langgraph-fastapi starter:
``load_dotenv`` before importing the graph, a module-level ``app`` built by
``create_app``, and uvicorn in a ``main()`` guard.
"""

import logging
import os
import warnings
from pathlib import Path

from dotenv import load_dotenv

# Load agent/.env before importing graph — matches the starter's ordering so
# env is in place before anything reads it. Does not override variables the
# environment (e.g. docker compose) already provides.
load_dotenv(Path(__file__).parent / ".env")

from ag_ui_langgraph import add_langgraph_fastapi_endpoint
from copilotkit import LangGraphAGUIAgent
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import uvicorn

from graph import build_graph

logger = logging.getLogger("uvicorn.error")

AGENT_NAME = "bi_agent"
SPA_ORIGIN = "http://localhost:5173"


def create_app() -> FastAPI:
    """Build the FastAPI app: CORS, /health, and the /copilotkit AG-UI route."""
    if not os.getenv("OPENROUTER_API_KEY"):
        logger.error(
            "OPENROUTER_API_KEY is not set (agent/.env). /health keeps serving; "
            "chat requests will fail until a key is provided."
        )

    app = FastAPI(title="bi-ai-ui agent")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[SPA_ORIGIN],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        # Strict contract for the walking skeleton: exactly this JSON body,
        # independent of the helper's /copilotkit/health which returns more.
        return {"status": "ok"}

    add_langgraph_fastapi_endpoint(
        app=app,
        agent=LangGraphAGUIAgent(
            name=AGENT_NAME,
            description="bi-ai-ui chat agent: answers business questions in plain text.",
            graph=build_graph(),
        ),
        path="/copilotkit",
    )
    return app


app = create_app()


def main() -> None:
    """Run the uvicorn server."""
    port = int(os.getenv("PORT", "8123"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
    main()

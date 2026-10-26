"""FastAPI wrapper exposing the LangGraph agent over AG-UI.

Structure mirrors the official CopilotKit langgraph-fastapi starter's
agent/main.py: load .env first, build the app, self-defined /health that
returns exactly {"status": "ok"}, then the AG-UI endpoint.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load agent/.env BEFORE importing graph — anything reading
# OPENROUTER_API_KEY / TEXT_MODEL must see the file's values (starter order).
for env_path in (Path(__file__).parent / ".env", Path(".env")):
    if env_path.is_file():
        load_dotenv(env_path)
        break
else:
    load_dotenv()

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ag_ui_langgraph import add_langgraph_fastapi_endpoint
from copilotkit import LangGraphAGUIAgent

from graph import graph

logger = logging.getLogger("bi-ai-ui.agent")

AGENT_NAME = "bi_agent"
SPA_ORIGIN = "http://localhost:5173"
AGENT_PORT = 8123
_NO_KEY_LOG = (
    "OPENROUTER_API_KEY is not set (agent/.env). /health keeps serving 200, "
    "but chat requests will fail until a key is provided."
)


def create_app() -> FastAPI:
    app = FastAPI(title="bi-ai-ui agent")

    # PRD v1.1: the SPA talks to this server directly from the browser, so
    # the Vite dev origin must be allowed on /copilotkit (incl. preflight).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[SPA_ORIGIN],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Own /health instead of the helper's /copilotkit/health: the acceptance
    # contract is exactly {"status": "ok"} with no extra keys.
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    add_langgraph_fastapi_endpoint(
        app=app,
        agent=LangGraphAGUIAgent(
            name=AGENT_NAME,
            description=(
                "bi-ai-ui agent: answers natural-language questions about the "
                "dashboard and composes UI components."
            ),
            graph=graph,
        ),
        path="/copilotkit",
    )
    return app


app = create_app()

# No-key boot must still serve /health (acceptance #4) — log the reason now
# so the operator sees it in `docker compose logs agent`.
if not os.getenv("OPENROUTER_API_KEY"):
    logger.error(_NO_KEY_LOG)


def main():
    """Run the uvicorn server."""
    uvicorn.run("server:app", host="0.0.0.0", port=AGENT_PORT)


if __name__ == "__main__":
    main()

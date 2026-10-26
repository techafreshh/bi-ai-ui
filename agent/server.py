"""FastAPI server exposing the LangGraph agent as a CopilotKit/AG-UI endpoint.

Server shape mirrors the official CopilotKit example
`examples/integrations/langgraph-fastapi/agent/main.py`: load_dotenv first,
self-defined `/health` (exact `{"status": "ok"}` contract), then the
ag-ui-langgraph FastAPI endpoint registration at `/copilotkit`.
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load agent/.env before importing graph so env vars are visible at first use.
load_dotenv()

from ag_ui_langgraph import add_langgraph_fastapi_endpoint  # noqa: E402
from copilotkit import LangGraphAGUIAgent  # noqa: E402

from graph import graph  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bi-ai-agent")

# SPA dev origin (PRD §9: server-side key only, direct browser -> agent calls).
CORS_ORIGINS = ["http://localhost:5173"]


def create_app() -> FastAPI:
    app = FastAPI(title="bi-ai-agent")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    add_langgraph_fastapi_endpoint(
        app=app,
        agent=LangGraphAGUIAgent(
            name="bi_agent",
            description="Business analytics assistant for the bi-ai-ui app.",
            graph=graph,
        ),
        path="/copilotkit",
    )
    return app


def _log_missing_key() -> None:
    # Acceptance #4: without a key the server still boots and /health serves 200;
    # only chat round-trips will fail until the key is provided.
    if not os.getenv("OPENROUTER_API_KEY"):
        logger.error(
            "OPENROUTER_API_KEY is not set (agent/.env). /health keeps serving; "
            "chat requests will fail until a key is provided."
        )


app = create_app()
_log_missing_key()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8123)

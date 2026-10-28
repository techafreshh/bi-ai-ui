"""FastAPI wrapper exposing the LangGraph agent over AG-UI.

Structure mirrors the official CopilotKit langgraph-fastapi starter's
agent/main.py: load .env first, build the app, self-defined /health that
returns exactly {"status": "ok"}, then the AG-UI endpoint. Boot is
DB-less by contract (issue #70): no seeder runs here, /health never waits
on a database, and read_state degrades when data/app.db is absent.
"""

import argparse
import logging
import os
import socket
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

# E402 on the imports below is structural: load_dotenv() above must run
# before any third-party/agent import so OPENROUTER_API_KEY/TEXT_MODEL are
# visible at graph build time (module docstring) — never move them up.
import uvicorn  # noqa: E402
from ag_ui_langgraph import add_langgraph_fastapi_endpoint  # noqa: E402
from copilotkit import LangGraphAGUIAgent  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

try:  # package-style entry (uvicorn agent.server:app from the repo root)
    from agent.graph import graph
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    from graph import graph

logger = logging.getLogger("bi-ai-ui.agent")

AGENT_NAME = "bi_agent"
# The SPA's browser-visible origin. Overridable so a remapped Docker stack
# (e.g. web on :12308) passes the matching CORS origin without code edits.
SPA_ORIGIN = os.getenv("SPA_ORIGIN") or "http://localhost:5173"
# Issue contract pins 8123 as the default; --host/--port flags and the
# AGENT_[HOST|PORT]/PORT env vars exist so harnesses and platform deploys can
# remap the bind without code changes.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8123
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


def _open_listeners(host: str, port: int) -> list[socket.socket] | None:
    """Pre-bind listening sockets, preferring a dual-stack IPv6 socket.

    A single AF_INET6 socket with IPV6_V6ONLY=0 answers both ::1 and
    127.0.0.1, so loopback probes succeed regardless of the client's
    address family. Without IPv6 support (or for a custom host) return
    None and let uvicorn do its regular bind.
    """
    if host not in ("0.0.0.0", "::", ""):
        return None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", port))
        sock.listen(128)
        sock.set_inheritable(True)
        return [sock]
    except OSError:
        return None


def main() -> None:
    """Run the uvicorn server (blocks until the loop exits)."""
    parser = argparse.ArgumentParser(description="bi-ai-ui agent server")
    parser.add_argument(
        "--host",
        default=os.getenv("AGENT_HOST") or os.getenv("HOST") or DEFAULT_HOST,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.getenv("AGENT_PORT") or os.getenv("PORT") or DEFAULT_PORT
        ),
    )
    args = parser.parse_args()

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    uvicorn.Server(config).run(sockets=_open_listeners(args.host, args.port))


if __name__ == "__main__":
    main()

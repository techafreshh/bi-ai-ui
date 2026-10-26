"""FastAPI wrapper exposing the LangGraph agent over AG-UI.

Structure mirrors the official CopilotKit langgraph-fastapi starter's
agent/main.py: load .env first, build the app, self-defined /health that
returns exactly {"status": "ok"}, then the AG-UI endpoint. Boot also
self-seeds the demo database in a daemon thread when data/app.db is absent
(runtime machines check the repo out fresh) — /health never waits on it.
"""

import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
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

try:  # package-style entry (uvicorn agent.server:app from the repo root)
    from agent import db
    from agent.graph import graph
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    import db
    from graph import graph

logger = logging.getLogger("bi-ai-ui.agent")

AGENT_NAME = "bi_agent"
SPA_ORIGIN = "http://localhost:5173"
# Issue contract pins 8123 as the default; --host/--port flags and the
# AGENT_[HOST|PORT]/PORT env vars exist so harnesses and platform deploys can
# remap the bind without code changes.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8123
_NO_KEY_LOG = (
    "OPENROUTER_API_KEY is not set (agent/.env). /health keeps serving 200, "
    "but chat requests will fail until a key is provided."
)

# One seed attempt per process (create_app runs again on test imports);
# tmp files are pid-suffixed so concurrent boots never clobber each
# other's in-progress database.
_SEED_STARTED = threading.Event()
GENERATOR_PATH = Path(__file__).resolve().parent / "scripts" / "generate_data.py"


def _seed_database_worker(tmp_path: Path, target_path: Path) -> None:
    """Generate the seed database to a tmp file, then atomically move it in.

    The tmp file is the point: a partial database from a killed generator
    must never become the live data/app.db that read_state opens."""
    logger.info("seeding demo database: generating %s", tmp_path)
    try:
        result = subprocess.run(
            [sys.executable, str(GENERATOR_PATH), "--db-path", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"generator exited rc={result.returncode}: "
                f"{(result.stderr or result.stdout)[-500:]}"
            )
        os.replace(tmp_path, target_path)
        logger.info("seeding demo database: done -> %s", target_path)
    except Exception as exc:  # noqa: BLE001 - seeding must never kill boot
        logger.error("seeding demo database failed: %s", exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_seed_database() -> None:
    """Kick off background seeding when the demo database is absent.

    Issue #10 runs the graph against "the seeded DB", but runtime machines
    check the repo out fresh and seeding is in neither the factory setup
    nor the start script — so the server does it itself, in a daemon thread
    (generation takes seconds; readiness probes still answer instantly and
    read_state's degradation covers the seeding window). Idempotent: skips
    when data/app.db already exists or this process already started a
    seed."""
    db_path = Path(db.DEFAULT_DB_PATH)
    if db_path.exists() or _SEED_STARTED.is_set():
        return
    _SEED_STARTED.set()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_pattern = f".{db_path.name}.*.seed-tmp"
    for stale in db_path.parent.glob(tmp_pattern):
        # Only one agent process serves a checkout, so any tmp still lying
        # around at boot is a dead process's partial file — sweep it so a
        # crashed seed cannot hold disk space forever.
        stale.unlink(missing_ok=True)
    tmp_path = db_path.with_name(f".{db_path.name}.{os.getpid()}.seed-tmp")
    threading.Thread(
        target=_seed_database_worker,
        args=(tmp_path, db_path),
        name="seed-database",
        daemon=True,
    ).start()


def create_app() -> FastAPI:
    app = FastAPI(title="bi-ai-ui agent")

    # Runtime checkouts ship without data/app.db; the graph needs the seed
    # (issue #10's acceptance queries it). Background, never blocking boot.
    _ensure_seed_database()

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

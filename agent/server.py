"""FastAPI wrapper exposing the LangGraph agent over AG-UI.

Structure mirrors the official CopilotKit langgraph-fastapi starter's
agent/main.py: load .env first, build the app, self-defined /health that
returns exactly {"status": "ok"}, then the AG-UI endpoint. Boot is
DB-less by contract (issue #70): no seeder runs here, /health never waits
on a database, and read_state degrades when data/app.db is absent.
Boot is empty by default (issue #71): no automatic seeding of any kind
runs at boot — with no active dataset GET /data/datasets reports
{"active_id": None, "datasets": []}.
"""

import argparse
import logging
import os
import socket
import uuid
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
from fastapi import FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

try:  # package-style entry (uvicorn agent.server:app from the repo root)
    from agent.graph import graph
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    from graph import graph

try:  # package-style entry (uvicorn agent.server:app from the repo root)
    from agent.sandbox import ALLOWED_TABLES, load_bytes
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    from sandbox import ALLOWED_TABLES, load_bytes

try:  # package-style entry (uvicorn agent.server:app from the repo root)
    from agent import datasets
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    import datasets

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

# Fallback string for UPLOAD_MAX_MB (issue #74): decimal megabytes capping
# POST /data/upload bodies, mirrored from config.py's registry row. Read
# per request (never import time) so tests and operators can retune it via
# the environment without reimporting the server.
_UPLOAD_MAX_MB_FALLBACK = "25"


def _upload_max_bytes():
    """Byte cap for upload bodies: UPLOAD_MAX_MB decimal megabytes.

    Non-numeric or empty values fall back to the default instead of 500ing
    the upload (the env-contract tests pin the same fallback in config.py
    and agent/.env.example)."""
    raw = os.getenv("UPLOAD_MAX_MB", _UPLOAD_MAX_MB_FALLBACK)
    if not raw or not raw.strip():
        raw = _UPLOAD_MAX_MB_FALLBACK
    try:
        return int(float(raw) * 1024 * 1024)
    except (TypeError, ValueError):
        return int(float(_UPLOAD_MAX_MB_FALLBACK) * 1024 * 1024)

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

    # Analyst CSV landing (issue #74): multipart `file` + `table`. Everything
    # is validated in memory — table allowlist, UPLOAD_MAX_MB size cap,
    # exact-header allowlist and strict int/ISO-date coercion — before
    # sandbox.py creates any file, so every 400 leaves no `.db` behind.
    # Later reads go through db.connect_ro() against the returned sandbox
    # id; the upload itself never touches DB_PATH (activation is a later
    # issue).
    @app.post("/data/upload", status_code=201)
    async def upload(
        file: UploadFile | None = File(None),
        table: str | None = Form(None),
    ):
        if table not in ALLOWED_TABLES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"unknown table {table!r}; must be one of "
                        f"{sorted(ALLOWED_TABLES)}"
                    )
                },
            )
        raw = await file.read() if file is not None else b""
        max_bytes = _upload_max_bytes()
        if len(raw) > max_bytes:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"file too large: {len(raw)} bytes exceeds "
                        f"the {max_bytes}-byte upload limit"
                    )
                },
            )
        try:
            # load_bytes decodes UTF-8 (fallback: 400 below) and validates
            # the whole body before creating the sandbox file.
            dataset_id = uuid.uuid4().hex
            _dest, rows, data_end = load_bytes(table, raw, dataset_id)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return {
            "id": dataset_id,
            "table": table,
            "rows": rows,
            "data_end": data_end,
        }

    # Single-active world-switch (issue #75): the registry behind
    # datasets.list_datasets() scans data/sandboxes/*.db live per request,
    # so uploads (issue #74) appear with no registration step and the
    # honest-empty contract (issue #71) holds — with no active dataset the
    # body is still exactly {"active_id": None, "datasets": []}.
    @app.get("/data/datasets")
    async def list_datasets():
        return datasets.list_datasets()

    # Explicit activation (issue #75): a known id moves the process-wide
    # active path every later db.connect() resolves, so all subsequent
    # queries read only the newly active file. Unknown, missing or
    # non-string ids are 404 {"error"} — never 400/422 — and leave the
    # active pointer untouched (set_active returns False without mutating
    # on anything it does not know).
    @app.post("/data/active")
    async def activate_dataset(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - any unparseable body is 404
            body = None
        dataset_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            return JSONResponse(
                status_code=404, content={"error": "missing dataset id"}
            )
        if datasets.set_active(dataset_id):
            return {"active_id": dataset_id}
        return JSONResponse(
            status_code=404,
            content={"error": f"unknown dataset {dataset_id!r}"},
        )

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

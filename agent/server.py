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
import sys
import uuid
from datetime import date
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
    from agent.sandbox import ALLOWED_TABLES, _parse_text, load_bytes, load_tables
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    from sandbox import ALLOWED_TABLES, _parse_text, load_bytes, load_tables

try:  # package-style entry (uvicorn agent.server:app from the repo root)
    from agent import datasets
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    import datasets

try:  # package-style entry (uvicorn agent.server:app from the repo root)
    from agent import catalog, quality, queries, questions
except ModuleNotFoundError:  # script-style entry (python3 server.py from agent/)
    import catalog
    import quality
    import queries
    import questions

# Manual-run composition (issue #81): hand-driven path through the same
# renderer with no decider — picker values (metric + window + segment)
# composed into a validated layout. Trendables render a line_chart,
# cohorts a 50-capped data_table, data_quality a data_quality_card.
_RUN_TRENDABLES = frozenset(
    {
        "net_new_arr",
        "mrr",
        "nrr",
        "churn_rate",
        "gross_churned_arr",
        "active_accounts",
        "active_accounts_eom",
    }
)
_RUN_COHORTS = frozenset({"churn_rate", "gross_churned_arr"})
_RUN_SEGMENTS = ("All", "Startup", "Growth", "Enterprise")
_RUN_TABLE_COLUMNS = ["segment", "mrr", "tenure_months", "plan"]


def _run_kpi_tiles(start, end, range_label):
    """Four KPI tiles mirroring graph._kpi_tiles but decider-free: reads
    queries.kpis directly for the window and the previous quarter, labels
    embedding the ISO range verbatim, NRR delta absolute, the rest
    relative (0.0 on a zero base)."""
    kpis = queries.kpis(start, end)
    previous = queries.kpis(*questions.previous_quarter((start,)))

    def tile(label, key, absolute=False):
        current = float(kpis[key])
        prior = float(previous[key])
        if absolute:
            delta = current - prior
        else:
            delta = (current - prior) / prior if prior else 0.0
        return {
            "label": f"{label} ({range_label})",
            "value": round(current, 2),
            "delta": round(delta, 4),
        }

    tiles = [
        tile("Net-new ARR", "net_new_arr"),
        tile("MRR", "mrr"),
        tile("NRR", "nrr", absolute=True),
    ]
    accounts = int(kpis["accounts"])
    prior_accounts = int(previous["accounts"])
    accounts_delta = (
        (accounts - prior_accounts) / prior_accounts if prior_accounts else 0.0
    )
    tiles.append(
        {
            "label": f"Accounts ({range_label})",
            "value": accounts,
            "delta": round(accounts_delta, 4),
        }
    )
    return tiles

logger = logging.getLogger("bi-ai-ui.agent")

AGENT_NAME = "bi_agent"
# Fallback default for SPA_ORIGIN (issue #79), mirrored from config.py's
# registry row: the comma-separated browser-origin allowlist for the
# agent's CORS middleware. The Tauri shell origin rides alongside the Vite
# dev origin so the desktop build passes preflight without code edits;
# a remapped Docker stack (e.g. web on :12308) overrides via the env.
_SPA_ORIGIN_FALLBACK = "http://localhost:5173,tauri://localhost"
# Fallback default for DATASOURCE_RO (issue #79), mirrored from
# config.py's registry row: read-only unless the operator opts out.
_DATASOURCE_RO_FALLBACK = "1"

# Spellings that count as read-only for DATASOURCE_RO (case-insensitive,
# whitespace-stripped); anything else — including "0" or empty — means
# the datasource may be writable, and the server refuses to boot.
_READONLY_VALUES = ("1", "true", "yes")


def _spa_origins():
    """CORS allowlist from SPA_ORIGIN: comma-split, stripped, empties
    dropped, order preserved. Empty or unset falls back to the default
    list instead of locking the SPA out."""
    raw = os.getenv("SPA_ORIGIN", _SPA_ORIGIN_FALLBACK)
    if not raw or not raw.strip():
        raw = _SPA_ORIGIN_FALLBACK
    origins = [part.strip() for part in raw.split(",") if part.strip()]
    if not origins:
        origins = [part.strip() for part in _SPA_ORIGIN_FALLBACK.split(",")]
    return origins


def _datasource_is_readonly():
    """True when DATASOURCE_RO holds a read-only spelling (issue #79)."""
    raw = os.getenv("DATASOURCE_RO", _DATASOURCE_RO_FALLBACK)
    return (raw or "").strip().lower() in _READONLY_VALUES


def _require_readonly_datasource():
    """Fail-fast read-only guard (issue #79): with DATASOURCE_RO set to
    anything but a read-only spelling the process exits non-zero with a
    read-only message on stderr before binding or listening."""
    if _datasource_is_readonly():
        return
    raw = os.getenv("DATASOURCE_RO", _DATASOURCE_RO_FALLBACK)
    print(
        f"refusing to boot: DATASOURCE_RO={raw!r} is not read-only — "
        "the agent serves read-only queries and must not start with a "
        "writable datasource",
        file=sys.stderr,
    )
    raise SystemExit(1)
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
    _require_readonly_datasource()
    app = FastAPI(title="bi-ai-ui agent")

    # PRD v1.1: the SPA talks to this server directly from the browser, so
    # the allowlist origins must be allowed on /copilotkit (incl.
    # preflight) — the Vite dev origin first, then the Tauri shell origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_spa_origins(),
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

    # Deterministic hand-driven path through the same renderer (issue
    # #81): picker values (metric + window + segment) composed server-side
    # into a validated layout with no decider. Validation order is
    # missing params, then the YAML-compiled metric whitelist, then the
    # ISO window, then the segment allowlist, then the active-dataset
    # probe — every failure is 400 {"error"} before any I/O. Success is
    # a validated A2UILayout dict (kpi_row first, line_chart for
    # trendables, 50-capped data_table for cohorts, data_quality_card for
    # data_quality, insight_text last) with deterministic slotCopy and
    # never a decisions key.
    @app.get("/data/run")
    async def run(
        metric: str | None = None,
        start: str | None = None,
        end: str | None = None,
        segment: str | None = "All",
    ):
        if metric is None or not str(metric).strip():
            return JSONResponse(
                status_code=400,
                content={"error": "missing required query parameter: metric"},
            )
        if start is None or not str(start).strip():
            return JSONResponse(
                status_code=400,
                content={"error": "missing required query parameter: start"},
            )
        if end is None or not str(end).strip():
            return JSONResponse(
                status_code=400,
                content={"error": "missing required query parameter: end"},
            )
        metric = str(metric).strip()
        start = str(start).strip()
        end = str(end).strip()
        if metric not in queries._METRIC_FUNCS:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"unknown metric {metric!r}; must be one of "
                        f"{sorted(queries._METRIC_FUNCS)}"
                    )
                },
            )
        try:
            canon_start = date.fromisoformat(start).isoformat()
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"window start is not a valid ISO date: {start!r}"
                },
            )
        try:
            canon_end = date.fromisoformat(end).isoformat()
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"window end is not a valid ISO date: {end!r}"
                },
            )
        if canon_start > canon_end:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"window start {canon_start} is after "
                        f"window end {canon_end}"
                    )
                },
            )
        start, end = canon_start, canon_end
        seg = segment if isinstance(segment, str) else "All"
        seg = seg.strip() if isinstance(seg, str) else "All"
        if seg not in _RUN_SEGMENTS:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"unknown segment {segment!r}; must be one of "
                        f"{list(_RUN_SEGMENTS)}"
                    )
                },
            )
        segment = seg
        try:
            queries.latest_event_date()
        except Exception:  # noqa: BLE001 - unreadable DB reads as empty
            return JSONResponse(
                status_code=400, content={"error": "no active dataset"}
            )
        try:
            queries.metric_value(metric, start, end)
        except ValueError as exc:
            return JSONResponse(
                status_code=400, content={"error": str(exc)}
            )
        except Exception:  # noqa: BLE001 - unreadable DB reads as empty
            return JSONResponse(
                status_code=400, content={"error": "no active dataset"}
            )
        range_label = f"{start}..{end}"
        try:
            tiles = _run_kpi_tiles(start, end, range_label)
            items: list[dict] = [
                {"type": "kpi_row", "props": {"kpis": tiles}}
            ]
            if metric in _RUN_TRENDABLES:
                items.append(
                    {
                        "type": "line_chart",
                        "props": {"series": metric, "range": range_label},
                    }
                )
            if metric in _RUN_COHORTS:
                seg_filter = None if segment == "All" else segment
                detail_rows = queries.churn_cohort_detail(
                    (start, end), segment=seg_filter, limit=50
                )[:50]
                table_rows = [
                    {
                        "segment": row["segment"],
                        "mrr": row["mrr"],
                        "tenure_months": row["tenure_months"],
                        "plan": row["plan"],
                    }
                    for row in detail_rows
                ]
                items.append(
                    {
                        "type": "data_table",
                        "props": {
                            "columns": list(_RUN_TABLE_COLUMNS),
                            "rows": table_rows,
                        },
                    }
                )
            if metric == "data_quality":
                items.append(
                    {
                        "type": "data_quality_card",
                        "props": {
                            "checks": quality.run_checks(window=(start, end))
                        },
                    }
                )
            items.append(
                {
                    "type": "insight_text",
                    "props": {
                        "title": f"{metric} insights \u2014 {range_label}",
                        "body": "",
                    },
                }
            )
            slot_copy: dict[str, str] = {
                "kpis.title": f"{metric} {range_label} ({segment})",
                "insight_text.body": (
                    f"{metric} from {start} to {end} "
                    f"(segment {segment}). KPIs and trend are "
                    f"window-scoped; the cohort table is filtered to "
                    f"{segment}."
                ),
                "summary": f"{metric} {range_label} segment {segment}.",
            }
            if metric in _RUN_TRENDABLES:
                slot_copy["line_chart.title"] = (
                    f"{metric} trend ({range_label})"
                )
            layout = {
                "v": 1,
                "layout": "grid",
                "items": items,
                "slotCopy": slot_copy,
            }
        except Exception:  # noqa: BLE001 - unreadable DB reads as empty
            return JSONResponse(
                status_code=400, content={"error": "no active dataset"}
            )
        validated = catalog.A2UILayout.model_validate(layout)
        return validated.model_dump()

    # Server-side sample miniature (issue #76): assembles ONE sandbox DB
    # from all four SAMPLES_DIR/*.csv files, registers it with source
    # "sample" and activates it in one call. Each CSV is fully validated
    # through sandbox._parse_text BEFORE any file is created, so every 400
    # leaves no `.db` behind; a missing samples dir (or a missing stem
    # file) is 404 {"error"}. The samples dir resolves from SAMPLES_DIR
    # (the docker-compose agent mount) defaulting to the repo-root
    # samples/ dir anchored from this file, mirroring db.py/datasets.py.
    @app.post("/data/sample")
    async def load_sample():
        raw_samples = os.getenv("SAMPLES_DIR", "")
        samples_dir = (
            Path(raw_samples.strip())
            if raw_samples and raw_samples.strip()
            else Path(__file__).resolve().parents[1] / "samples"
        )
        if not samples_dir.is_dir():
            return JSONResponse(
                status_code=404,
                content={"error": f"samples directory not found: {samples_dir}"},
            )
        rows_by_table = {}
        for table in ALLOWED_TABLES:
            csv_path = samples_dir / f"{table}.csv"
            if not csv_path.is_file():
                return JSONResponse(
                    status_code=404,
                    content={"error": f"sample file not found: {csv_path.name}"},
                )
            try:
                text = csv_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"{csv_path.name} is not valid UTF-8"},
                )
            try:
                rows, _ = _parse_text(table, text, str(csv_path))
            except ValueError as exc:
                return JSONResponse(status_code=400, content={"error": str(exc)})
            rows_by_table[table] = rows
        dataset_id = uuid.uuid4().hex
        dest = load_tables(rows_by_table, dataset_id)
        datasets.register(dataset_id, dest, "sample")
        datasets.set_active(dataset_id)
        profile = datasets.profile_db(dest)
        return {
            "active_id": dataset_id,
            "tables": profile["tables"],
            "rows": profile["rows"],
            "data_end": profile["data_end"],
        }

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
    _require_readonly_datasource()
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

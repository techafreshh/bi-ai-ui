# bi-ai-ui

Chat with your business data: ask a question in plain English and the agent answers by composing a dashboard from a fixed catalog of pre-built components — it never invents markup.

[Features](#features) • [Architecture](#architecture) • [Prerequisites](#prerequisites) • [Quickstart](#quickstart) • [Usage](#usage) • [Configuration](#configuration) • [Verification](#verification) • [Limitations](#limitations)

![Q3 sales dashboard: KPI tiles and ARR trend chart](docs/screenshot-q3-dashboard.png)

## Features

- **Natural-language BI chat.** A LangGraph agent classifies each question, assembles a validated layout from the component catalog, and streams a grounded narrative alongside it.
- **Deterministic mock data.** A seeded generator builds ~2.9M rows of B2B-SaaS data (accounts plus daily subscription, revenue, and usage events over the 24 months ending 2026-09-30) with three planted shapes: a Q3-2026 growth ramp, an Aug-10-2026 pricing-change churn spike (40 Growth accounts), and a Sept 8–21 Startup ingest bug.
- **Keyless boot.** The stack starts with no API key; the decider falls back to a deterministic heuristic and the narrative is KPI-grounded copy until you add a key.
- **Two read legs, no decider.** `GET /data/run` composes a validated layout for a metric + window + segment, and `POST /data/query` serves metric spot-checks plus a guarded SELECT-only SQL hatch.
- **Read-only second source.** `DATASOURCE_URL` selects a Postgres backend with template parity to SQLite; the app never writes to it.

## Architecture

```
React SPA (Vite) :5173           FastAPI agent (LangGraph) :8123     OpenRouter
┌─────────────────────────┐  AG-UI SSE  ┌───────────────────────┐  OpenAI-compatible
│ CopilotKit sidebar +    │ ──────────▶ │ POST /copilotkit      │ ─────────────▶
│ fullscreen canvas       │ ◀────────── │ five-node graph:      │  text model (fill)
└─────────────────────────┘ token deltas│ read_state → decide   │
         ▲ shared state                 │ → layout → query → fill│ ◀─── decisions ──┐
         └── a2ui · decisions · status  └───────────┬───────────┘   modality        │
                                                     ▼               (agent/.env key)
                                      ┌───────────────────────────┐
                                      │ SQLite — data/app.db      │
                                      │ (gitignored) or per-      │
                                      │ dataset sandboxes         │
                                      └───────────────────────────┘
```

- `agent/graph.py` — the five-node agent graph (`read_state` loads window KPIs and data-quality stats, `decide` classifies the question, `layout` builds the component draft, `query` runs slot-scoped SQL, `fill` streams the narrative).
- `agent/catalog.py`, `agent/decision/` — the validated component catalog and the probabilistic decision client (with heuristic fallback).
- `agent/queries.py`, `agent/quality.py`, `agent/semantics/metrics.yaml` — the eight queryable metrics (`net_new_arr`, `mrr`, `nrr`, `churn_rate`, `active_accounts`, `data_quality`, `gross_churned_arr`, `active_accounts_eom`).
- `agent/server.py` — HTTP surface: `GET /health`, `POST /copilotkit`, `POST /data/upload`, `GET /data/datasets`, `GET /data/run`, `POST /data/sample`, `POST /data/active`, `POST /data/query`.
- `web/src/` — the SPA: CopilotKit sidebar chat, fullscreen canvas rendering the streamed layout, header dataset picker and decision log.
- `samples/` — the reviewer-scale CSV miniature behind `POST /data/sample` (see `samples/README.md` for provenance).

## Prerequisites

- [Docker Compose](https://docs.docker.com/compose/) (the canonical run path; host-only `uv`/`npm` paths are for development, not setup).
- An [OpenRouter API key](https://openrouter.ai/keys) only if you want model-written narratives — the stack boots and answers without one.

## Quickstart

```bash
git clone https://github.com/techafreshh/bi-ai-ui.git && cd bi-ai-ui
cp agent/.env.example agent/.env
docker compose up
```

- SPA: <http://localhost:5173> — chat in the sidebar (`Type a message…`).
- Agent: <http://localhost:8123> — `GET /health` returns exactly `{"status": "ok"}`.
- Boot holds **no dataset**: the canvas shows `No dataset loaded — upload CSV or load sample` until you load one. Load the sample miniature with either of these (no rebuild, no restart):

```bash
curl -X POST http://localhost:8123/data/sample
```

or press **Load sample** on the empty-state card / header DatasetPicker (same endpoint).

To generate the full deterministic database instead:

```bash
./scripts/reset_data.sh
docker compose up -d
```

> [!NOTE]
> Without an `OPENROUTER_API_KEY` in `agent/.env`, both services still boot and `/health` keeps returning 200. The decider uses its deterministic heuristic transport and the narrative is KPI-grounded copy instead of model text.

> [!TIP]
> Running away from 5173/8123? Set `AGENT_HOST_PORT`, `WEB_HOST_PORT`, `VITE_AGENT_URL=http://localhost:<host-port>/copilotkit`, and `SPA_ORIGIN=http://localhost:<web-host-port>` in a repo-root `.env` (gitignored), then `docker compose up -d --build`. `VITE_AGENT_URL` is the URL the *browser* calls, and `SPA_ORIGIN` must match the SPA origin or CORS preflight rejects chat. `scripts/verify_demo.py` follows via `AGENT_URL`/`WEB_URL`.

## Usage

### Chat (the golden paths)

Open <http://localhost:5173> and type into the sidebar prompt:

| You type | The canvas assembles |
|---|---|
| `Show me sales in Q3` | Four KPI tiles (net-new ARR, MRR, NRR, accounts over `2026-07-01..2026-09-30`) plus an ARR trend line chart. |
| `Why did churn spike in August?` | A variance callout (churn-rate move vs the trailing 6-month average, cause: the Aug-10 pricing change) above a ~40-row cohort table. Press **Investigate** to drill into the Growth cohort. |
| `Is the data clean?` | A data-quality card (Freshness ok, Duplicates fail, Nulls fail) under a banner naming the Sept 8–21 Startup ingest bug, with a **Report an issue** button. Open the header **Decision log** to see each decision's probability, model, and latency. |
| `draw me a dragon` | Off-catalog questions render only the insight-text fallback — the agent never invents components. |

![Churn drill-down: variance callout with the Investigate CTA and cohort table](docs/screenshot-churn-drilldown.png)

![Data-quality card with the failing-checks banner and Report an issue](docs/screenshot-data-quality.png)

![Decision log drawer](docs/screenshot-decision-log.png)

### Manual picker (no chat needed)

The header MetricPicker composes the same validated layout server-side with no decider: pick a metric, window, and segment and press **Run** (`GET /data/run?metric=&start=&end=&segment=`).

### HTTP legs

```bash
# Metric spot-check (same compiler the NL path uses; data_quality returns the checks list)
curl -X POST http://localhost:8123/data/query \
  -H 'Content-Type: application/json' \
  -d '{"metric": "net_new_arr", "start": "2026-07-01", "end": "2026-09-30"}'

# Guarded SQL hatch: exactly one leading SELECT, capped rows, read-only connection
curl -X POST http://localhost:8123/data/query \
  -H 'Content-Type: application/json' \
  -d '{"sql": "SELECT * FROM accounts LIMIT 5"}'

# Upload a CSV table (multipart `file` + `table`; 25 MB cap) or switch datasets
curl -X POST http://localhost:8123/data/upload -F file=@accounts.csv -F table=accounts
curl http://localhost:8123/data/datasets
curl -X POST http://localhost:8123/data/active \
  -H 'Content-Type: application/json' -d '{"id": "<dataset-id>"}'
```

Queryables are compiled from `agent/semantics/metrics.yaml` (`net_new_arr`, `mrr`, `nrr`, `churn_rate`, `active_accounts`, `data_quality`, `gross_churned_arr`, `active_accounts_eom`).

### Postgres second source

```bash
DATASOURCE_URL=postgresql://user:pass@localhost:5432/bi scripts/load_postgres.sh
```

Then restart the agent with the same `DATASOURCE_URL` and prove parity with `python3 scripts/verify_data_slice.py --target postgres`. The Postgres source is read-only by session (`default_transaction_read_only=on`) and the server refuses to boot unless `DATASOURCE_RO` holds a read-only spelling.

### Random-profile data

To test the agent against data it was never staged for (organic growth, a seed-picked spike month/segment, a seed-picked 14-day bug window):

```bash
./scripts/reset_data.sh --restart --profile random --seed 7
```

The generator prints the drawn spike/bug windows so you know what to ask. The same seed always rebuilds the identical dataset.

## Configuration

Server-side only: copy `agent/.env.example` to `agent/.env` (gitignored) and restart the agent (`docker compose up -d agent`, no rebuild). Every value in the example equals the code default, so copying is behavior-neutral. Every variable below is read by `agent/` code (registry: `agent/config.py`).

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | _(empty)_ | OpenRouter key for chat; without it the agent still boots and answers via heuristic fallback. |
| `TEXT_MODEL` | `openai/gpt-6-luna` | Chat model the fill node streams the narrative from. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible base for the text model and the chat decision fallback. |
| `DECISION_ENDPOINT` | `https://openrouter.ai/api/alpha/decisions` | Full decisions-modality URL; overrides the base-derived path. |
| `DECISION_MODEL` | `~perplexity/pplx-decider-flash-latest` | Decisions-modality model; reported verbatim as `decisions[].model`. |
| `DECISION_MODEL_CHOICE` | _(empty)_ | Per-type override for choice questions; empty = use `DECISION_MODEL`. |
| `DECISION_MODEL_NOUL` | _(empty)_ | Per-type override for noul questions; empty = use `DECISION_MODEL`. |
| `DECISION_MODEL_SCORE` | _(empty)_ | Per-type override for score questions; empty = use `DECISION_MODEL`. |
| `DECISION_FALLBACK_MODELS` | `openai/gpt-6-luna,anthropic/claude-haiku-5.5` | Comma-separated chat-completions fallback chain (JSON schema); members must be chat models. |
| `DECISION_THRESHOLD` | `0.6` | Endorsement line: components below it are cut from the layout. |
| `DECISION_TIMEOUT_S` | `8.0` | Per-request timeout (seconds) for both decision transports. |
| `SPA_ORIGIN` | `http://localhost:5173,tauri://localhost` | Comma-separated browser-origin allowlist for the agent's CORS middleware. |
| `AGENT_HOST` / `HOST` | `0.0.0.0` | Bind host for `python server.py` (`AGENT_HOST` wins). |
| `AGENT_PORT` / `PORT` | `8123` | Bind port for `python server.py` (`AGENT_PORT` wins). |
| `DB_PATH` | _(empty)_ | Active per-dataset sandbox SQLite file; empty falls back to `data/app.db`. |
| `DATASOURCE_URL` | _(empty)_ | Empty = local SQLite; a `postgresql://` value selects the read-only Postgres source. |
| `DATASOURCE_RO` | `1` | Read-only guard: only `1`/`true`/`yes` boot, anything else refuses to start. |
| `SQL_ROW_LIMIT` | `50` | Default row cap for the churn cohort/driver queries. |
| `SQL_TIMEOUT_S` | `5.0` | SQLite connect timeout in seconds. |
| `UPLOAD_MAX_MB` | `25` | Size cap (decimal MB) for `POST /data/upload` bodies; larger files are rejected with 400. |
| `SAMPLES_DIR` | _(empty)_ | Directory `POST /data/sample` reads its CSVs from; empty = repo-root `samples/`. |

Zero-code model swap — edit `agent/.env` only, then `docker compose up -d agent`:

```bash
DECISION_MODEL=inception/mercury-decide   # or per-type: DECISION_MODEL_CHOICE=...
TEXT_MODEL=openai/gpt-6-luna
DECISION_THRESHOLD=0.95
```

## Verification

```bash
docker compose up -d            # agent :8123 + web :5173 (empty by default — load a DB first)
./scripts/reset_data.sh         # or: curl -X POST http://localhost:8123/data/sample
python3 scripts/verify_demo.py  # 15 stdlib-only HTTP/state probes, exit 0 on green
python3 scripts/verify_data_slice.py --target sqlite  # upload → triple-query parity → off-catalog safety, prints `parity: ok`
```

```bash
cd web && npm run lint && npm run typecheck && npm run test
cd agent && uv run ruff check . && uv run pytest
npm run screenshots --prefix web   # recapture docs/*.png from the live app
```

## Limitations

- **Keyless mode degrades.** Without `OPENROUTER_API_KEY`, decisions come from the deterministic heuristic and narratives are template-grounded, not model-written.
- **Postgres is read-only and sample-scoped.** The second source serves the `samples/` rows loaded by `scripts/load_postgres.sh`; all DDL lives in that script and the app opens read-only sessions only.
- **Random-profile numbers differ.** The demo script's exact figures (Q3 ramp, 40-row cohort, Sept bug) hold for the seeded profile; on `--profile random` the same queries stream the same component pairs composed from whatever the data holds, and `verify_demo.py`'s planted-shape probes are expected to miss there.
- **Full data is generated, not committed.** `data/` stays gitignored; `agent/scripts/generate_data.py --db-path <path>` reproduces full-scale DBs and `samples/` plus the generator are the only distribution artifacts.

This project is MIT licensed — see [LICENSE](LICENSE).

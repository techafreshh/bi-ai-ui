# bi-ai-ui

AI business-intelligence dashboard where a chat agent answers natural-language
queries by composing the UI from a fixed catalog of pre-built, design-system
components — the agent classifies each query, assembles a validated layout,
and streams the narrative from an OpenRouter text model; it never invents
markup. Full product spec: [.agents/PRDs/PRD.md](.agents/PRDs/PRD.md).

## Architecture

```
React SPA (Vite) :5173           FastAPI agent (LangGraph) :8123     OpenRouter
┌─────────────────────────┐  AG-UI SSE  ┌───────────────────────┐  OpenAI-compatible
│ CopilotKit v2 provider  │ ──────────▶ │ POST /copilotkit      │ ─────────────▶
│ HttpAgent · CopilotSidebar│◀──────────│ single-node chat graph│   text model
└─────────────────────────┘ token deltas │ (TEXT_MESSAGE_* out)  │ ◀─────────────
                                         └───────────┬───────────┘ (key: agent/.env)
                                                     │
                                                     ▼
                                      ┌───────────────────────────┐
                                      │ SQLite — data/app.db      │
                                      │ deterministic seeded mock │
                                      │ accounts + daily events   │
                                      └───────────────────────────┘
```

- The browser connects directly to the FastAPI agent (no Node runtime in
  between, PRD v1.1); the agent's CORS allows only the Vite dev origin
  (`agent/server.py`).
- The agent is a single LangGraph `chat` node streaming an OpenRouter
  text model as an OpenAI-compatible endpoint (`agent/graph.py`).
- Mock B2B-SaaS data lives in `data/app.db` (gitignored), generated
  deterministically by `python agent/scripts/generate_data.py`: ~5–8k
  accounts plus 24 months (2024-10-01 → 2026-09-30) of daily
  `subscription_events` / `revenue_events` / `usage_events` with three
  planted shapes — a Q3-2026 net-new-ARR growth ramp, an Aug-10-2026
  pricing-change churn spike (~40 Growth-segment accounts adopt a new plan
  and cancel through 2026-09-30), and a Sept 8–21 ingest bug (exact
  duplicates plus NULL-amount usage rows, Startup segment only); the schema
  is documented in the script's docstring and opened via `agent/db.py`.
  `agent/quality.py` runs the freshness / duplicates / nulls data-quality
  checks over an inclusive date scope (default: the trailing 30 days ending
  at the latest observed event day), returning `{name, status, score}` rows
  for the "Is the data clean?" card.
- Both services run from `docker-compose.yml` (web dev server :5173,
  agent :8123).

## Quickstart

Requires only Docker — no API key is needed to boot (chat needs one).

```bash
git clone https://github.com/techafreshh/bi-ai-ui.git && cd bi-ai-ui
cp agent/.env.example agent/.env
docker compose up
```

- SPA: http://localhost:5173 — open it and chat in the sidebar ("ping").
- Agent: http://localhost:8123 — `GET /health` returns exactly `{"status": "ok"}`.
- Without an OpenRouter key in `agent/.env`, both services still boot and
  `/health` keeps returning 200; only chat requests fail (the agent log names
  the missing variable).

## Environment

```bash
cp agent/.env.example agent/.env   # server-side only; agent/.env is gitignored
```

Set your OpenRouter key (create one at https://openrouter.ai/keys) in
`agent/.env` and restart the agent. The key never reaches the browser.

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | yes (for chat) | OpenRouter key for the text model |
| `TEXT_MODEL` | no (default `openai/gpt-6-luna`) | Model id sent to OpenRouter |
| `OPENROUTER_BASE_URL` | no (default `https://openrouter.ai/api/v1`) | Point the model at another OpenAI-compatible endpoint |
| `DECISION_THRESHOLD` | no (default `0.6`) | Min probability for `above_threshold` flagging on decision answers |

Every variable above exists in [`agent/.env.example`](agent/.env.example).

## Demo script

TODO — scripted golden-path demo for reviewers, finalizing in a later issue.

## Model swap guide

TODO — swapping the model via `TEXT_MODEL` only (env change, no code), later issue.

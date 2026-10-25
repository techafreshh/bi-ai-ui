# bi-ai-ui

AI business-intelligence dashboard where a chat agent answers natural-language
questions by **composing the UI from a fixed catalog of pre-built components** —
the "Jev generative UI" pattern. The agent never invents markup: a decision
layer classifies each query with typed, probabilistic questions (Choice / Noul /
Score), a validated A2UI-style layout is assembled from the catalog, and the
text model only writes the narrative copy.

## Architecture

```
React SPA (Vite, :5173)                    LangGraph agent (FastAPI, :8123)
┌──────────────────────────────────┐   ┌─────────────────────────────────────┐
│ CopilotKitProvider (v2)          │   │ read_state  route the query         │
│   HttpAgent ─────────────────────┼──▶│ decide      5 typed decisions       │
│ useAgentState ◀── STATE_SNAPSHOT ┼───│ layout      skeleton from picks     │
│ Canvas: registry → components    │   │ query       whitelisted SQL         │
│ DecisionDrawer (log)             │   │ fill        narrative + status      │
│ CopilotSidebar (chat)            │   └─────────────────────────────────────┘
└──────────────────────────────────┘        │
       Recharts · Tailwind                  ▼
       SQLite (deterministic seed 42)  OpenRouter (decisions + text)
```

```
bi-ai-ui/
├─ web/                            # React SPA (Vite + TypeScript + Tailwind)
│  └─ src/
│     ├─ components/catalog/       # 6 catalog components + registry.ts
│     ├─ components/               # Canvas, DecisionDrawer, Suggestions
│     ├─ lib/a2ui.ts               # Zod layout schema + validator
│     ├─ lib/useAgentState.ts      # agent state subscription + send path
│     └─ App.tsx                   # provider, shell, drawer
├─ agent/                          # LangGraph python (CopilotKit scaffold)
│  ├─ graph.py                     # read_state → decide → layout → query → fill
│  ├─ server.py                    # FastAPI + CORS + /copilotkit + /health
│  ├─ decision/client.py           # Choice/Noul/Score + transport chain
│  ├─ catalog.py                   # Pydantic catalog + A2UI validation
│  ├─ queries.py                   # whitelisted parameterized SQL
│  ├─ quality.py                   # DQ checks (freshness/duplicates/nulls)
│  ├─ store.py                     # seed-42 SQLite generator (+ data/ output)
│  └─ scripts/generate_data.py     # manual CLI shim (--reset)
├─ scripts/verify_demo.py          # §11 probe suite (stdlib only)
└─ docs/                           # README screenshots
```

The five graph nodes map to the demo flow: **routing** (deterministic keyword
classifier over six routes), **decisions** (five typed questions per query,
transport chain with a deterministic local policy), **layout composition**
(threshold 0.6 drops sub-threshold components; an empty composition falls back
to a single `insight_text` card), **queries** (parameterized templates against
the seeded SQLite store), and the **narrative fill** (text model when an API
key is set, canned per-route copy otherwise).

## Quickstart

```bash
cp .env.example agent/.env   # add OPENROUTER_API_KEY — optional, see below
docker compose up
```

- SPA: <http://localhost:5173>
- Agent health: <http://localhost:8123/health> → `{"status": "ok"}`
- Agent AG-UI endpoint: <http://localhost:8123/copilotkit>

**Without an API key**, the agent boots, seeds the deterministic demo database
(24 months of revenue events, ~6,400 accounts), and answers every query in
demo mode: decisions come from the local policy table, the narrative from the
canned per-route copy (marked with a `⚠️` prefix in chat). All of the
verification below passes in this mode — the demo needs zero network egress.

**With `OPENROUTER_API_KEY` set**, decisions route through the OpenRouter
decisions endpoint (falling back to chat-model structured outputs), and the
narrative streams live from the `TEXT_MODEL`.

## Demo script (90 seconds)

Type each prompt into the sidebar, watch the canvas assemble, then inspect the
decision log. Everything below runs identically with or without the API key.

**1. Q3 growth (real trend)** — send `Show me sales in Q3`.
The canvas assembles a **KPI row** (net-new ARR, closing MRR, NRR, active
accounts) and a **line chart** spanning exactly `2026-07-01..2026-09-30` with
the campaign and pricing-change annotations. `insight_text` scored below the
0.6 threshold and was dropped — that is the agent choosing, and not padding.

![Q3 dashboard: KPI row + daily net-new ARR line chart](docs/screenshot-q3-dashboard.png)

**2. Churn spike (real anomaly)** — send `Why did churn spike in August?`.
A **variance callout** quantifies churned MRR vs the trailing 6-month baseline
(cause: the Aug 10 pricing change on the Growth tier) and a **cohort table**
lists the 42 churned accounts (account, segment, MRR, tenure, plan). Press
**Investigate** on the callout — the agent runs a drill-down and re-composes
the view around the cohort.

![Churn drilldown: variance callout + cohort table with Investigate](docs/screenshot-churn-drilldown.png)

**3. Is the data clean? (bad data)** — send `Is the data clean?`.
The **data quality card** surfaces the planted September ingest bug:
`duplicates: fail`, `nulls: fail` (8% NULL amounts, exact-duplicate copies,
Startup segment only), `freshness: ok`. The Noul question *“real business
change or data problem?”* scores **p = 0.94**, which raises the warning banner
and arms the **Report an issue** button.

![Data quality: failing duplicates/nulls checks with Report an issue](docs/screenshot-data-quality.png)

**4. Open the decision log.** Click **Decision log** in the header. Every
query carries the full typed record: question, options with probability bars,
pick, model, and real measured latency. Off-catalog asks (`Draw me a dragon`)
never render unknown markup — every component decision scores below the
threshold, so the layout degrades to a single fallback insight card.

![Decision log drawer over the data-quality path](docs/screenshot-decision-log.png)

## Model swap guide

All model configuration is env-driven — swap models with **zero code changes**
by editing `agent/.env` and restarting the agent (`docker compose restart agent`).

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | — (optional) | OpenRouter key; absent ⇒ deterministic demo mode |
| `TEXT_MODEL` | `openai/gpt-6-luna` | narrative (chat completions, streamed) |
| `DECISION_MODEL` | `~perplexity/pplx-decider-flash-latest` | decisions endpoint model |
| `DECISION_CHAT_MODEL` | `anthropic/claude-haiku-5.5` | structured-output fallback model |
| `DECISION_TRANSPORT` | `auto` | `auto` (chain when keyed, else local) · `live` (always chain) · `mock` (always local) |
| `DECISION_THRESHOLD` | `0.6` | drop components whose p falls below this |

Walkthrough examples:

- **Try the Jev decision model**: set `DECISION_MODEL=typesafe/jev-1.14`,
  restart. Decisions now flow through the OpenRouter decisions endpoint with
  that model id (visible per-record in the decision log).
- **Cheapest narrator**: `TEXT_MODEL=anthropic/claude-haiku-5.5`.
- **Force deterministic behavior** (CI, screenshots): `DECISION_TRANSPORT=mock`
  — identical to the keyless demo mode.

The transport chain inside one query is: OpenRouter decisions endpoint → chat
completions with `response_format: json_schema` → local policy (deterministic,
always available). Each decision record names the model that produced it, so a
mixed fallback is visible in the log rather than silently swallowed.

## Verification

With the stack up (`docker compose up`):

```bash
python3 scripts/verify_demo.py
```

…runs the PRD §11 checklist as 17 black-box probes over the real AG-UI wire —
health contract, SPA serving, CORS, Q3 (`kpi_row` + `line_chart` covering the
exact Q3 range), churn (callout + 42-row cohort), data quality
(`duplicates: fail`, `nulls: fail`, `freshness: ok`, Noul p = 0.94), ≥4
drawer-complete decisions per golden query, off-catalog fallback, and clean
run termination. Exit code 0 + one `[ok]` line per check:

```
[ok  ] q3_line_chart_range: range='2026-07-01..2026-09-30', 92 points (Jul 1 → Sep 30, 92 days)
...
0 of the checks failed
```

Lint / typecheck / tests:

```bash
cd web  && npm run lint && npm run typecheck && npm run test
cd agent && uv sync && uv run pytest && uv run ruff check .   # from the repo root: uvx ruff check agent
```

## Ports & configuration

Port `8123` for the agent is pinned per the CopilotKit LangGraph quickstart;
`5173` is the Vite default. Both are fixed in `docker-compose.yml` and the
respective configs. The agent reads `agent/.env` (copy from `.env.example`,
at the repo root); the SPA needs no configuration — it points at
`http://localhost:8123/copilotkit` (`web/src/App.tsx`).

Demo data lives in `data/app.db` (gitignored). It is generated on agent start
(seed 42, byte-deterministic) or manually:

```bash
python agent/scripts/generate_data.py --reset
```

## Dependency pinning notes

- **zod v3 pin** — the CopilotKit SDK line we pin (`@copilotkit/react-core`
  1.x, which bundles `zod-to-json-schema` and friends) targets the zod 3
  API. Unversioned installs can pull zod v4, which does not satisfy those
  peers, so `web/package.json` pins the v3 line (`zod@^3.25.76` plus an npm
  override) and commits `package-lock.json`.
- **`ag-ui-langgraph==0.0.44`** — 0.0.43 is incompatible with
  `copilotkit==0.1.96` (every run 500s; see the comment in
  `agent/pyproject.toml`). Both lockfiles (`web/package-lock.json`,
  `agent/uv.lock`) are committed so `docker compose up` is reproducible.
- **`agent/uv.lock` is the single source of truth** for the Python
  environment — the Docker image installs with `uv sync --frozen` (exactly the
  locked versions, no pip involved), and hosts use the same `uv sync`. Run
  ruff inside `agent/` with `uv run ruff check .`, or from the repo root with
  `uvx ruff check agent`; the ruff version itself is pinned in
  `agent/pyproject.toml`.
- **`ruff`** — pinned into `agent/pyproject.toml` (`>=0.8,<1.0`, currently
  0.17.2) so `uv sync --frozen` in the Docker image resolves the same lint
  toolchain the checks were run with.

## Development without Docker

```bash
# web
cd web && npm install && npm run dev        # :5173

# agent (demo mode works without a key; add OPENROUTER_API_KEY for live mode)
cd agent && uv sync && uv run uvicorn server:app --port 8123
```

Screenshots (headless chromium against the running stack):

```bash
cd web && npx playwright install chromium && npm run screenshots   # writes docs/*.png
```

# PRD — bi-ai-ui · AI BI Dashboard with Jev-Style Generative UI

**Version:** 1.1 · **Date:** 2026-10-20 · **Status:** Draft approved for implementation
**v1.1 changelog:** frontend switched from Next.js to a React (Vite) SPA; `CopilotKit` now connects directly to the agent's FastAPI `/copilotkit` endpoint.

---

## 1. Executive Summary

bi-ai-ui is an AI business-intelligence dashboard in which a chat agent answers natural-language queries by **composing the UI from a fixed catalog of pre-built components** — the "Jev generative UI" pattern (CopilotKit blog, treasury example). A decision model classifies each query with typed questions (Choice / Noul / Score) returning **calibrated probabilities**; the agent assembles the answers into a validated A2UI-style nested-JSON layout; a text model writes only the narrative copy. The agent never invents markup.

On seeded B2B-SaaS mock data, three golden demo paths: **"Show me sales in Q3"** → KPI tiles + line chart (real growth trend); **"Why did churn spike?"** → variance callout + filtered cohort table + Investigate CTA (real anomaly: Aug-2026 pricing-change cancels); **"Is the data clean?"** → data-quality card exposing a planted Sept ingest bug (duplicate/null amounts, one segment, two weeks).

**MVP goal:** a polished local demo (`docker compose up`, one command) where a reviewer watches the dashboard assemble itself and inspects every probabilistic decision in a decision-log drawer.

## 2. Mission

**Prove that generative UI works when the agent *chooses* from components a design system already trusts — it never draws.**

Core principles:

1. **Choose, don't invent** — every rendered component is a validated catalog member.
2. **Typed, probabilistic decisions** — Choice/Noul/Score with threshold routing and default fallbacks.
3. **Radically observable** — every decision is visible (question, options, probabilities, pick, model, latency).
4. **Cheap and parallel** — ≤7 decision calls per query at ~$0.01/M tokens; one text call for copy; skeleton renders before copy fills.
5. **Portfolio polish** — a 90-second demo communicates the whole pattern.

## 3. Target Users

- **Reviewer (primary)** — hiring manager / tech lead; runs the demo live or clones it. Comfortable with web apps and SQL-ish concepts. Need: grasp pattern + quality instantly. Pain: typical AI dashboards answer in prose, hallucinate layouts, or dump SQL.
- **Owner-developer (secondary)** — extends the catalog, swaps models via env, reads logs/state.

## 4. MVP Scope

### In Scope

**Core functionality**

- [x] CopilotKit chat sidebar (beUI chat components: Prompt Input, Message Bubble, Streaming Response) + dashboard canvas
- [x] 6-component catalog: `kpi_row`, `line_chart`, `variance_callout`, `data_table`, `data_quality_card`, `insight_text`
- [x] LangGraph Python agent graph: `read_state → decide → layout → query → fill`
- [x] `DecisionClient`: Choice/Noul/Score to OpenRouter decisions-modality models, parallel fan-out
- [x] Default decision model `~perplexity/pplx-decider-flash-latest` (pplx-decider-flash-v1.2, $0.01/M); fallback chain: `inception/mercury-decide:free` → chat model with structured outputs (`openai/gpt-6-luna` or `anthropic/claude-haiku-5.5`, $0.10/M)
- [x] Probability threshold (default 0.6) + default-view fallback for sub-threshold/invalid layouts
- [x] Decision-log drawer: question, options, probabilities, pick, model, latency
- [x] Both rendering surfaces: inline mini-cards in chat + full components in canvas
- [x] Investigate CTA → follow-up agent drill-down run

**Data**

- [x] Deterministic seeded SQLite: 24 months of daily subscription/revenue events, ~5–8k accounts, with the three planted shapes (§7)
- [x] Data-quality checks (freshness, duplicates, nulls) feeding Noul questions

**Technical**

- [x] Env-configurable `DECISION_MODEL`, `TEXT_MODEL`, `OPENROUTER_API_KEY`, `DECISION_THRESHOLD`
- [x] Git from day 1 (scaffold-first commit history)
- [x] `docker-compose.yml` + one-command boot; unit tests (layout validator, DecisionClient parsing, DQ checks)
- [x] README: architecture diagram, demo script, model-swap guide

### Out of Scope

- [ ] Live deployment (Vercel + hosted agent) — post-MVP
- [ ] Auth / multi-user / chat-history persistence
- [ ] Real warehouse connectors (Postgres/BigQuery)
- [ ] Token-streaming chart updates (skeleton→fill suffices)
- [ ] `typesafe/jev-1.14` as primary decision model (env-swappable, not the target)
- [ ] Classifier-accuracy eval harness (blog disclaims accuracy claims)

## 5. User Stories

1. As a **reviewer**, I want to ask "Show me sales in Q3" so that KPI tiles (net-new ARR, MRR, NRR, accounts) and a Jul–Sep line chart render in the canvas with a mini-card in chat. *Jev log: kpi_row p=0.91, line_chart p=0.88.*
2. As a **reviewer**, I want "Why did churn spike in August?" answered with a variance callout (Δ vs 6-mo baseline), a table of ~40 churned accounts (segment, MRR, tenure, plan), and an **Investigate** button that drills into the cohort.
3. As a **reviewer**, I want "Is the data clean?" to show a data-quality card with check scores; the Sept 8–21 duplicate/null-amount bug (Startup segment) drives Noul("data problem?")≈0.94, a warning banner and report-an-issue affordance.
4. As a **reviewer**, I want to open the **decision log** and see every Jev question with probabilities, pick, model and latency — proof the UI is classified, not hallucinated.
5. As a **reviewer**, I want off-catalog asks ("draw me a dragon") to render a graceful fallback view so I see the control layer is safe.
6. As the **owner-developer**, I want decision/text models in `.env` so I can swap in `typesafe/jev-1.14` or another model with zero code changes.
7. As the **owner-developer**, I want `docker compose up` so clone→demo is one command.
8. As an **engineer**, I want layout JSON validated against Pydantic/Zod schemas so invalid layouts can never reach the renderer.

## 6. Core Architecture & Patterns

```
React SPA (Vite) :5173               LangGraph Python agent :8123 (AG-UI/FastAPI)
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│ CopilotSidebar (beUI chat)    │──▶ read_state: aggregates + DQ stats
│  inline mini-cards            │  │ decide: N parallel decisions     │
│ Canvas A2UI renderer ─────────┼─── layout: pydantic-validated JSON │
│  registry → typed components  │◀── query: SQL for chosen slots     │
│ Decision drawer · theme toggle│  │ fill: text model → copy          │
└───────────────────────────────┘  └──────────────────────────────────┘
   Recharts · beUI · Tailwind            OpenRouter (decisions + chat)
   SQLite (deterministic, generated)
```

```
bi-ai-ui/
├─ web/                      # React SPA (Vite + TypeScript + Tailwind)
│  └─ src/
│     ├─ components/catalog/ # 6 catalog components + registry.ts
│     ├─ components/ui/      # beUI copies (chat, cards, table, alerts)
│     ├─ lib/a2ui.ts         # Zod layout schema + validator + fallback
│     └─ App.tsx             # providers, canvas, sidebar, decision drawer
├─ agent/                    # LangGraph Python (CopilotKit scaffold)
│  ├─ graph.py               # read_state → decide → layout → query → fill
│  ├─ server.py              # FastAPI app + CORS + /copilotkit endpoint
│  ├─ decision/client.py     # Choice/Noul/Score + fallback transports
│  ├─ catalog.py             # pydantic mirror of component catalog
│  ├─ queries.py             # SQL aggregates + filters
│  ├─ quality.py             # DQ checks
│  └─ scripts/generate_data.py
├─ data/app.db               # generated, gitignored
├─ docker-compose.yml
└─ README.md
```

**Design patterns:**

- **Component registry** — type→React component map; unknown type renders a fallback card, never an error.
- **Catalog over AG-UI** — component descriptions travel to the agent as part of its instructions.
- **Decision engine (GDE)** — register catalog → parallel decisions → validated layout → text fill.
- **Threshold + default fallback** — p<0.6 drops a component; empty layout falls back to a default insight view.
- **Skeleton-then-fill** — the layout streams first; copy slots fill after the text model responds.
- **Shared schemas** — Pydantic ⇄ Zod mirrors; the frontend re-validates every layout.

## 7. Tools / Feature Specs

### Catalog components

| Component | beUI base | Key props (slots) | Picked when |
|---|---|---|---|
| `kpi_row` | Card + Number Animation | 3–4 KPIs `{label, value, delta}` | summary metrics |
| `line_chart` | Card + Recharts | series, range, annotations | trend questions |
| `variance_callout` | Animated Alert | metric, delta, baseline, cause, CTA | real anomaly on "why" |
| `data_table` | virtualized Table | columns, rows ≤50, filters | entity-level detail |
| `data_quality_card` | Alert + Badge list | checks `[{name, status, score}]` | quality diagnostics |
| `insight_text` | text block | title, body | narrative / fallback |

### Decision question set (illustrative)

- `Choose` — "Which catalog components does this query need?" (multi, options = catalog) → e.g. kpi_row 0.91, line_chart 0.88, data_table 0.31.
- `Noul` — "Is this movement real business change vs. a data problem?" → gates warning banners / report-an-issue.
- `Noul` — "Is there an anomaly worth a callout?"
- `Score` — "Data-quality confidence 0–1" → gates data_quality_card emphasis + investigation CTA.

Same decision model for all question types by default; per-type override via env.

### Planted data shapes (seeded)

1. **Q3 growth (real)** — Aug 1 campaign launch; smooth, strong net-new ARR through the quarter.
2. **Churn spike (real)** — Aug 10 pricing change; ~40 accounts (Growth segment → new plan) churn through September.
3. **Ingest bug (bad data)** — Sep 8–21 duplicated + null-amount `usage` events, Startup segment only; DQ results: duplicates breach, null% > 2%, freshness OK.

## 8. Technology Stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React 19 (Vite) + TypeScript + Tailwind | `npm create vite@latest` SPA — no SSR, no Next.js |
| Chat/wiring | `@copilotkit/react-core`, `@copilotkit/react-ui` | pin exact versions (zod v3 peer-dep pin noted in docs); `CopilotKit` provider's `runtimeUrl` points directly at the agent's `/copilotkit` FastAPI endpoint — no separate Node runtime |
| UI kit | beUI copies (React + Tailwind + Motion) | Chat (Prompt Input, Message Bubble, Streaming Response, AI Sidebar), Card, Number Animation, Animated Alert, virtualized Table, theme toggle |
| Charts | Recharts | wrapped in beUI Cards (check beUI `/charts` first) |
| Agent | LangGraph Python + CopilotKit AG-UI endpoint, port 8123 | quickstart scaffold |
| Decision model | `~perplexity/pplx-decider-flash-latest` | $0.01/M in, 4B, 262k ctx, decisions output modality |
| Fallbacks | `inception/mercury-decide:free`; `typesafe/jev-1.14` ($0.042/M); `openai/gpt-6-luna` / `anthropic/claude-haiku-5.5` ($0.10/M) via `response_format: json_schema` | identical `DecisionClient` interface |
| Text model | `openai/gpt-6-luna` via `langchain-openai` → OpenRouter base_url | narrative copy only |
| Data | SQLite (Python stdlib) + deterministic seeded generator | aggregates into agent state, never raw rows |
| Infra | docker-compose (frontend + agent) | one command |

## 9. Security & Configuration

- **No auth (MVP)** — local demo only; live-URL deployment is post-MVP with gated access.
- **Configuration:**
  - `agent/.env`: `OPENROUTER_API_KEY` (server-side only, gitignored), `DECISION_MODEL`, `TEXT_MODEL`, `DECISION_THRESHOLD=0.6`.
  - No secrets in the frontend; the SPA's `CopilotKit` provider connects directly to the agent's CopilotKit/AG-UI endpoint (FastAPI `CORSMiddleware` allowing the Vite dev origin `http://localhost:5173`).
- **Security scope:** in — env hygiene; JSON-schema validation both ends; read-only parameterized SQL with whitelisted query templates. out — authN/Z, audit logs, PII controls (mock data contains no PII).

## 10. API Specification

### DecisionClient (internal)

The one genuinely unverified contract — Phase-1 spike confirms the wire format:

```
POST OpenRouter decisions-modality endpoint
  { "state": <compact JSON snapshot>,
    "questions": [ { "type": "choice"|"noul"|"score", "id": "...", "prompt": "...", "options": [...] } ] }
→ { "answers": [ { "id": "...", "type": "...", "probabilities": {option: 0..1} | p | score } ] }
```

If the named decisions endpoint diverges, the transport swaps to chat completions with `response_format: json_schema` behind the identical interface. Fallback chain per §8.

### A2UI layout (agent → frontend via shared state)

```json
{ "v": 1, "layout": "grid",
  "items": [
    {"type": "kpi_row", "props": {"kpis": [{"label": "Net-new ARR", "value": 1200000, "delta": 0.18}]}},
    {"type": "line_chart", "props": {"series": "arr_daily", "range": "2026-07-01..2026-09-30"}},
    {"type": "variance_callout", "props": {"metric": "MRR churn", "delta": 0.42, "cause": "pricing change Aug 10", "cta": "investigate_churn_cohort"}}
  ],
  "slotCopy": {"kpis.title": "Q3 revenue took off"} }
```

Frontend re-validates; unknown type or invalid payload → fallback card + decision-log warning.

### Agent ↔ frontend

CopilotKit-compatible AG-UI endpoint exposed by the agent's FastAPI app (port 8123 pinned per quickstart); the SPA's `CopilotKit` provider points at `http://localhost:8123/copilotkit`. Shared state fields: `a2ui`, `decisions[]`, `status`. Investigate CTA = interactive component callback that sends a framed follow-up message to the agent.

## 11. Success Criteria

**MVP success = three golden paths working in one live session + drawer evidence + one-command boot.**

- [ ] "Show me sales in Q3" → `kpi_row` + `line_chart` in canvas, correct Q3 window (Jul–Sep 2026), mini-cards in chat
- [ ] "Why did churn spike in August?" → `variance_callout` + `data_table` (≤50 rows, right cohort) + working Investigate drill-down
- [ ] "Is the data clean?" → `data_quality_card` surfacing the Sept bug; Noul result shown and logged
- [ ] Decision drawer shows ≥4 decisions per query with probabilities, model, latency
- [ ] Off-catalog query never renders an unknown component (fallback observed)
- [ ] `docker compose up` boots frontend + agent with zero manual steps
- [ ] Unit tests green: layout validator (valid/invalid/unknown/sub-threshold), DQ checks, DecisionClient parsing (mock + live contract test)

**Quality indicators:** dark/light theme parity; desktop-first ≥1280px; skeleton appears <400ms; decisions complete <2.5s typical; demo cost <$0.05/session; lint/typecheck clean both sides; README demo script reproducible by a newcomer.

## 12. Implementation Phases

### Phase 0 — Foundations (day 1)
**Goal:** runnable skeleton.
**Deliverables:**
- [ ] Vite React SPA scaffold (`web/`) + CopilotKit packages wired to the agent's `/copilotkit` endpoint; LangGraph Python agent scaffold (`agent/`) per quickstart
- [ ] git init + first commits
- [ ] OpenRouter text-model smoke test wired
- [ ] beUI components copied; theme tokens; dark/light toggle
- [ ] docker-compose; README stub
**Validation:** dev servers + containers boot; chat round-trips a text reply.

### Phase 1 — Decision layer (days 2–3)
**Goal:** working `DecisionClient` + verified contract.
**Deliverables:**
- [ ] `decision/client.py`: Choice/Noul/Score with retries + timeouts
- [ ] Live contract spike vs `pplx-decider-flash` (capture real response shapes)
- [ ] Fallback transports (`inception/mercury-decide:free`; chat-model structured outputs)
- [ ] Unit tests
**Validation:** 20 sample question-set calls each <300ms; parse/threshold tests green.

### Phase 2 — Agent graph + data (days 4–6)
**Goal:** query → validated A2UI layout.
**Deliverables:**
- [ ] Data generator + 3 planted shapes + DQ checks
- [ ] Graph nodes: read_state / decide / layout / query / fill
- [ ] Pydantic catalog + layout schemas + threshold routing
- [ ] State streaming `a2ui` + `decisions[]`
**Validation:** 3 golden queries produce correct layout JSON in tests; invalid/unknown handled.

### Phase 3 — Frontend + polish (days 7–10)
**Goal:** the demo.
**Deliverables:**
- [ ] A2UI renderer + registry + fallback card
- [ ] 6 catalog components on beUI/Recharts
- [ ] Sidebar + inline mini-cards
- [ ] Decision drawer
- [ ] Investigate CTA drill-down
- [ ] Dark/light polish; README (diagram, demo script, model-swap guide)
**Validation:** §11 checklist live in browser; screenshots captured for README.

### Phase 4 — Hardening (days 11–12)
**Goal:** ship-ready repo.
**Deliverables:**
- [ ] Full test suite green (pytest + FE tests)
- [ ] Clean-clone docker run-through
- [ ] Seed reset script
- [ ] Final README + tidy commit history
**Validation:** fresh clone → `docker compose up` → §11 checks pass.

## 13. Future Considerations

- Live URL (Vercel frontend + hosted agent) with key-gated demo tenant.
- More components (cohort heatmap, funnels, forecast) and hierarchical classification (TypeSafe cookbooks pattern).
- A/B `typesafe/jev-1.14` selectable from the decision drawer.
- "Why this layout?" narration affordance; decision-log CSV export for PMs.
- Real warehouse connector + auth; eval harness comparing decision models on labeled scenarios.

## 14. Risks & Mitigations

1. **Uncertain OpenRouter decisions API contract** (supported_parameters empty; wire format may differ from chat completions) → Phase-1 contract spike with the real key; `DecisionClient` abstracts transports; chat-model structured-output fallback implements the identical interface; mock transport for tests.
2. **Cheap/free model latency or rate limits** → ≤7 parallel calls per query; 800ms per-question timeout resolving to default options; env-swappable model IDs.
3. **CopilotKit rapid evolution / scaffold drift** (zod v3 vs v4 pin, port 8123 pin per docs) → pin exact versions at scaffold; commit working lockfiles; verify against the official quickstart.
4. **Bad decision quality on ambiguous queries** → probability threshold + default view; layouts constrained to the catalog so failures stay valid; all decisions visible in the drawer for prompt tuning.
5. **Scope creep beyond the three golden paths** → the three paths are the contract; new components only after §11 is green; 12-day effort budget enforced.
6. **Mock-data shapes too subtle or unconvincing** → anomaly magnitudes asserted in Phase-2 tests before frontend work.

## 15. Appendix

- **Blog (pattern + GDE steps):** https://www.copilotkit.ai/blog/jev-generative-ui
- **CopilotKit quickstart (agent backend):** https://docs.copilotkit.ai/coagents/quickstart/langgraph (port 8123 pin; frontend is built as a React SPA per the "React (including React SPA)" frontend option, not the Next.js template)
- **OpenRouter decision models:** https://openrouter.ai/models?output_modalities=decisions (27 models; `pplx-decider-flash-v1.2` canonical 20261016; alias `~perplexity/pplx-decider-flash-latest`)
- **OpenRouter structured outputs:** https://openrouter.ai/docs/features/structured-outputs
- **beUI:** https://beui.dev/components (copy-paste source; Motion + Tailwind; AI Agents section; Pro tier not needed)
- **Assumptions:** Q3 = Jul–Sep 2026 (today: Oct 2026); "sales" ≈ revenue/MRR semantics; single-user local demo; English UI.

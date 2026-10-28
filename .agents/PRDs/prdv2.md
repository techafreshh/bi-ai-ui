# PRD — bi-ai-ui v2 · De-seeded Agent-Driven BI with Sandbox Datasets

**Version:** 2.0 · **Date:** 2026-10-28 · **Status:** Draft for review
**Supersedes:** `.agents/PRDs/PRD.md` v1.1 (Jev generative UI on seeded SQLite)
**Codename:** prdv2

---

## 1. Executive Summary

bi-ai-ui v2 removes the single most confusing part of v1: the hidden seeded SQLite database. In v1 a fresh clone booted DB-less and silently re-seeded ~143 MB via two hidden paths (a server background thread and a Docker entrypoint sync seed), then the demo, tests, and verification probes pinned exact choreographed shapes (Q3 2026-07-01..09-30 ramp, 40-row Growth churn spike, Sept 8–21 Startup ingest bug). A reviewer could never tell what was choreographed from what was real.

v2 makes **empty the honest default**. `docker compose up` boots with no dataset, `/health` stays green, and the canvas shows an empty state: upload a CSV, load a tiny in-repo sample, or point at Postgres — then query. The same data that used to be baked in ships as small sample CSVs (<1 MB) plus the existing deterministic generator as the full-data reproducer, so the repo stays open-source-safe and clonable.

The product pivots from "seeded demo that proves a mechanism" to "minimal BI tool with an agent inside". Users can use it as a **normal BI tool** (pick a dataset, pick a metric/window/segment, run, read tiles and tables) and can also use the **agent feature** (ask in natural language, watch the dashboard assemble from the same fixed 6-component catalog, inspect every probabilistic decision). Both modes render through the same validated A2UI layout and the same component registry — one render path, two entry points. The web version stays deliberately minimal; a future desktop port (Tauri/Electron-style, local agent sidecar, local files, offline) is prepared for but not built.

**v2 MVP goal:** a reviewer boots empty, uploads or loads a sample in under a minute, runs the same question three ways (natural language, metric picker, guarded SQL), and sees identical validated components with decision evidence — on seeded-shape and unplanted data alike.

---

## 2. Mission

**Prove generative UI generalizes: the agent chooses from components a design system trusts — on data it was never staged for — and a human can do the same task by hand through the same renderer.**

Core principles:

1. **Empty is honest** — no hidden seed, no silent regeneration; every number on screen traces to a named dataset the user loaded.
2. **One renderer, two drivers** — manual picks and agent picks both compile to the same Pydantic/Zod-validated A2UI layout; the control layer (fixed catalog, threshold cut, fallback card) never forks.
3. **Agent drives, human can override** — the agent remains the sole writer of agent layouts (read_state → decide → layout → query → fill), but every agent answer is reproducible by hand via the metric picker.
4. **Sandbox everything** — uploads land in isolated per-dataset sandboxes that can never pollute another dataset or a golden path.
5. **Minimal web, portable core** — the web shell stays thin (chat, canvas, pickers, upload); data access, validation, and env contracts stay sidecar-portable for desktop later.

---

## 3. Target Users

- **Reviewer (primary)** — hiring manager / tech lead / OSS visitor. Clones the repo or runs the demo, boots empty, loads a sample, asks the three golden questions plus one paraphrase, opens the decision log. Needs: zero-confusion boot, 90-second "upload → query → evidence" loop. Pain today: hidden seed + exact-string golden queries + heuristic-only default.
- **Analyst (secondary, new in v2)** — wants to use it as a small BI tool: load a CSV export, check quality, slice a metric by window/segment, hand the result to a teammate. Comfortable with dropdowns and basic SQL; not necessarily with code. Needs: dataset picker, metric picker, safe SQL escape hatch.
- **Owner-developer (continuing)** — extends metrics, adds a source, swaps models via env, prepares the desktop port. Needs: YAML metrics without code edits, `Source` protocol with Postgres second, machine-checked env contract, prod web artifact.

English UI, single-user local deployment, desktop-first ≥1280px carry over from v1.

---

## 4. MVP Scope

### De-seed & samples

- [ ] Remove all auto-seed paths (server background thread, entrypoint sync seed, `RESEED`, `reset_data.sh` seed path, `verify_demo.py` seed-wait + seeded pins)
- [ ] Honest empty state: no active dataset → canvas shows "No dataset loaded — upload CSV or load sample", degraded snapshot surfaced instead of zeros
- [ ] Ship `samples/` tiny CSVs (<1 MB total: accounts / subscription_events / revenue_events / usage_events slices) mirroring `SCHEMA_SQL` columns + planted invariants (one repriced-churn cluster, one dup/null week) with checksums
- [ ] Keep `agent/scripts/generate_data.py` as the full-data reproducer (`--db-path` into a sandbox), not a boot path; full CSVs (if wanted) ship as release assets, never committed blobs

### Sandbox datasets

- [ ] `POST /data/upload` (multipart CSV, size-capped, column-checked) → per-upload sandbox SQLite file (`./data/sandboxes/<id>.db`, `event_date` indexes recreated)
- [ ] `GET /data/datasets` (list: id, source, rows, data_end, seeded-vs-uploaded) + `POST /data/active {id}` switch; throwaway for v2 (vanish on restart acceptable, volume kept only for sandboxes)
- [ ] Dataset picker in web header (active dataset + row counts + data_end + profile line); empty-state upload dropzone
- [ ] `DB_PATH` env honored by `db.py` (default empty = no active DB); `queries.py` / `quality.py` take the active path, no logic change

### Dual-mode minimal BI (same renderer)

- [ ] Manual mode: metric + window + segment dropdowns + Run, composing `kpi_row` / `line_chart` / `data_table` through existing `parseA2UILayout` / `validateComponentProps` / `registry` / `A2UIRenderer`
- [ ] Agent mode (existing): chat path scoped to the active dataset (`snapshot.dataset_id`, window anchored on that DB's `latest_event_date`); Investigate prefix contract unchanged
- [ ] Minimal web image: multi-stage Dockerfile (`build → serve dist/`), devDeps/test/screenshot tooling out of the image; keep chat, canvas, 6 components, simplified decision drawer, pickers, upload

### Sources & semantics

- [ ] `agent/data/` `Source` protocol (`connect_ro`, `tables`, `latest_event_day`); `SqliteSource` moves current templates verbatim; `PostgresSource` (read-only, whitelisted templates, bound params only) as the confirmed second source
- [ ] `DATASOURCE_URL` (+ `DATASOURCE_RO=1`) in `config.py` / `.env.example` / `test_env_contract.py` (same 3-way pin as issue #19); default empty
- [ ] `agent/semantics/metrics.yaml` declaring the current metric set (name, definition, grain, format, owner) mirroring `queries.py` semantics; `queries.py` compiles it; unknown metric/column fails at YAML load, never at render; 2 new file-only metrics to prove the path (e.g. `gross_churned_arr`, `active_accounts_eom`); optional read-only Metrics viewer in header — **no UI editor in v2**

### Sandbox query runs (three interfaces, built in order)

- [ ] (1) Natural-language chat scoped to sandbox (existing decide → layout → query → fill, no new agent nodes)
- [ ] (2) Metric picker Run (deterministic, exercises the YAML layer directly)
- [ ] (3) Guarded SELECT-only SQL box (read-only conn, 50-row limit, statement timeout, single statement, no DDL/semicolons) for power users
- [ ] Paraphrase eval script (~20 paraphrases/path + 10 distractors, seeded + random seeds, heuristic vs `LIVE=1` live model), **report-only** in v2

### Out of Scope (deferred)

- [ ] Persistent dataset library across restarts (v2 sandboxes may be throwaway; persistence is the immediate follow-up)
- [ ] Dashboard save / duplicate / version / publish, dashboard builder, drag-drop authoring
- [ ] New chart types (bar/stacked/area/pie/scatter/heatmap/map/funnel/gauge/pivot), zoom/brush, exports (CSV/PNG/PDF), embeds, URL state
- [ ] Auth / multi-user / RBAC / row-level security / audit log / PII masking
- [ ] Scheduled refresh / subscriptions / threshold alerts / push API
- [ ] Scheduler-driven ingest, incremental ETL, multi-source joins
- [ ] Desktop build (Tauri/Electron scaffolding, sidecar bundling, signing, updater) — only prep work in v2
- [ ] Warehouse beyond Postgres; live-model recalibration gate (eval is report-only)
- [ ] YAML metrics UI editor (file edit + restart is the v2 path)

---

## 5. User Stories

1. As a **reviewer**, I want `docker compose up` to boot with no dataset and an honest empty state, so that I never mistake seeded choreography for product behavior. *Example: canvas reads "No dataset loaded — upload CSV or load sample"; `/health` is 200 throughout.*
2. As a **reviewer**, I want to load the in-repo sample and ask "Show me sales in Q3" to see `kpi_row` + `line_chart` over the sample's window with a mini-card and a one-line summary, so that the golden path is reproducible on demand instead of on boot. *Example: sample window `2026-07-01..2026-09-30`, 3–4 tiles, ARR trend chart.*
3. As an **analyst**, I want to upload my own events CSV into an isolated sandbox and ask "why did churn spike?" in plain words, so that I see whether the agent generalizes beyond the planted data. *Example: upload → dataset picker shows row counts + data_end → chat returns `variance_callout` + cohort table or an honest fallback, never another dataset's numbers.*
4. As an **analyst**, I want to pick metric + window + segment from dropdowns and press Run, so that I can answer the same question by hand when I don't trust (or don't want) the agent. *Example: metric `nrr`, window `2026-Q3`, segment `Growth` → same `kpi_row` tiles the agent would have built.*
5. As an **analyst**, I want a guarded SQL box (`SELECT` only, 50-row cap, timeout) over my sandbox, so that I have an escape hatch for questions the pickers can't express. *Example: `SELECT segment, COUNT(*) FROM accounts GROUP BY segment` runs read-only; `DROP TABLE` is rejected before any connection opens.*
6. As a **reviewer**, I want "Is the data clean?" on my upload to run the same three checks (freshness/duplicates/nulls) scoped to where the dirt actually is, so that quality evidence isn't diluted by a display window. *Example: `recent_problem_scope` names the dirty window + majority segment; card shows `duplicates:fail, nulls:fail` with the narrated detail line.*
7. As an **owner-developer**, I want to add a metric by editing `metrics.yaml` (no code change) and point the stack at Postgres with one env var, so that semantics and sources evolve without rewrites. *Example: add `gross_churned_arr` → restart agent → metric picker offers it; `DATASOURCE_URL=postgresql://…` → same queries green.*
8. As an **owner-developer**, I want the paraphrase eval report (paraphrases + distractors, seeded + random seeds, heuristic vs live) so that I know the generalization gap as a number before promising more. *Example: `scripts/eval_paraphrase.py` prints per-path accuracy + fallback rate; v2 does not gate merges on it.*

---

## 6. Core Architecture & Patterns

```
Minimal web (prod dist/ served, :5173)      FastAPI agent (:8123)              Sources
┌──────────────────────────────┐  AG-UI SSE  ┌──────────────────────────┐   ┌──────────────┐
│ Chat (agent mode)            │ ──────────▶ │ POST /copilotkit         │   │ Sandbox .db  │
│ Metric picker + Run (manual) │ ── REST ──▶ │  read_state → decide ─▶  │ ─▶│  SQLite file │
│ Guarded SQL box (manual)     │ ◀── state ──│  layout → query → fill   │   │ Postgres URL │
│ Canvas A2UI renderer (shared)│             │ POST /data/upload|active │◀──│ Samples/*.csv│
│ Dataset picker · empty state │             │ GET  /data/datasets      │   └──────────────┘
└──────────────────────────────┘             │ metrics.yaml compiler  │   OpenRouter
   One A2UI renderer, two drivers            └──────────────────────────┘   decisions+chat
```

- **Empty-by-default boot.** No background seeder, no entrypoint seed, no `RESEED`. `db.py` resolves the active path from `DB_PATH`/`DATASOURCE_URL` (empty = no DB). `read_state`'s existing degraded snapshot becomes user-visible empty state instead of silent zeros.
- **Sandbox isolation.** Every upload generates `<id>.db` under `./data/sandboxes/` (gitignored) with the three `event_date` indexes recreated. Queries and quality checks take the active sandbox path; cross-dataset reads are impossible by construction (one active path per process). v2 sandboxes are throwaway; the `datasets` list is in-memory + filesystem scan.
- **Source protocol (additive).** `agent/data/Source` wraps today's `db.connect` + `queries.*` templates as `SqliteSource` verbatim; `PostgresSource` re-implements the same method surface over a read-only role with the same static-template + bound-param discipline. Callers (`graph.py`, quality, eval) depend on the protocol, never on `sqlite3` directly.
- **Semantics file.** `agent/semantics/metrics.yaml` is the single registry of metric name → definition → grain → format. `queries.py` compiles entries to the existing static SQL (today's six metrics are the first six entries). `compact_snapshot_summary` inlines the active metric defs into decider prompts so the model stays grounded. Load-time validation (unknown column/metric → raise at import/startup).
- **Dual-mode, single renderer.** Manual mode never touches the decider: it builds an `A2UILayout` dict from picker values, runs it through the same `catalog.A2UILayout` (server) and `parseA2UILayout`/`validateComponentProps` (client), and renders via the same `registry` + `A2UIRenderer`. Agent mode is unchanged (decide → threshold cut → fallback) but carries `dataset_id` in the snapshot and anchors `resolve_window` on the active DB's `latest_event_date`.
- **Design patterns carried over:** fixed catalog (`extra="forbid"` / `.strict()`), threshold + fallback (p < 0.6 drops, empty → `insight_text`), skeleton-then-fill, deterministic degradation, dual `try: agent.X / except ModuleNotFoundError` imports, long-docstring modules.

```
bi-ai-ui/
├─ samples/                    # NEW: tiny CSVs (<1MB) + checksums + README
│  ├─ accounts.csv subscription_events.csv revenue_events.csv usage_events.csv
├─ web/                        # minimal shell: chat, canvas, pickers, upload, drawer-lite
│  └─ src/
│     ├─ components/catalog/   # unchanged 6 + registry
│     ├─ components/datasets/  # NEW: DatasetPicker, EmptyState, UploadDropzone
│     ├─ components/manual/    # NEW: MetricPicker, SqlBox
├─ agent/
│  ├─ data/                    # NEW: Source protocol + SqliteSource + PostgresSource
│  ├─ semantics/               # NEW: metrics.yaml + compiler/loader
│  ├─ server.py                # + /data/* routes, multi-origin CORS, no seeder
│  ├─ graph.py                 # snapshot.dataset_id, no logic change otherwise
│  ├─ scripts/generate_data.py # full-data reproducer (--db-path into sandbox)
├─ scripts/eval_paraphrase.py  # NEW: report-only generalization eval
```

---

## 7. Tools / Feature Specs

### 7.1 Samples (`samples/`)

Four CSVs with exactly the `SCHEMA_SQL` columns (`generate_data.py:218-250`): `accounts(id,name,segment,plan,arr_cents,created_at)`, `subscription_events(id,account_id,event_date,event_type,arr_delta_cents)`, `revenue_events(id,account_id,event_date,amount_cents)`, `usage_events(id,account_id,event_date,metric,units NULLABLE)`. One-month slice + ~20 churn rows with the paired pre-cancel `upgrade` repricing signature + one dup/null week (Startup-only) so all three golden paths reproduce on demand. Total <1 MB, `sha256sums.txt`, `README` with generator parity note (`--profile seeded` reproduces the full invariant set at 2.9M-row scale).

### 7.2 Upload & datasets

- `POST /data/upload`: multipart `file` (CSV) or a small JSON `{type, rows}` batch; 25 MB cap; header must match one of the four known tables; types coerced (`arr_cents` int, dates ISO); rejects on unknown column, bad date, or empty file before any write. Response: `{id, table, rows, data_end}`.
- `GET /data/datasets`: `[{id, source: "upload"|"sample"|"postgres", tables, rows, data_end}]`.
- `POST /data/active {id}`: switches the process active path (single-active for v2).
- Web: header `DatasetPicker` (select + profile line: source, rows, `data_end`, seeded-vs-uploaded), canvas `EmptyState` with dropzone + "load sample" button when no dataset is active.

### 7.3 Manual mode

- `MetricPicker`: metric (from `metrics.yaml` names), window (quarter buttons Q1–Q4 + ISO range inputs, resolved by the same `resolve_window` regexes), segment (All/Startup/Growth/Enterprise). Run → `GET /data/run?metric&window&segment` (new, read-only) → server composes `kpi_row` (+ `line_chart` for trendable metrics, `data_table` capped at 50 for cohort metrics) → same validation → canvas renders with a "manual" badge and no `decisions[]`.
- `SqlBox`: textarea + Run; server allows exactly one `SELECT` (case-insensitive leading keyword, no `;`, no CTE writes, keyword blocklist: `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/PRAGMA`), `LIMIT 50` appended when absent, 5 s timeout; results render in `data_table` (columns = cursor keys). Errors render as `insight_text` with the message, never a crash.

### 7.4 Agent mode (unchanged logic, rescoped data)

Same five nodes, same four questions, same threshold/fallback/Investigate contracts. Only changes: `snapshot.dataset_id` plumbed from the active dataset; `read_state` anchors on the active source's `latest_event_day()`; `recent_problem_scope` scans the active sandbox; empty active dataset short-circuits to the empty-state layout (still `RUN_FINISHED`, `decisions[]` empty, drawer shows "no dataset").

### 7.5 Metrics file

```yaml
# agent/semantics/metrics.yaml (v2 initial contents = today's six)
- name: net_new_arr
  definition: SUM(arr_delta_cents) over subscription_events in window
  grain: day
  format: money
  owner: queries.kpis
- name: mrr
  definition: active ARR at window end / 12
  format: money
- name: nrr
  definition: cohort ARR at end / cohort ARR at start
  format: ratio
- name: churn_rate
  definition: churned ARR in month-to-date / active ARR at month start
  format: percent
- name: active_accounts
  definition: active-account count at window end
  format: count
- name: data_quality
  definition: freshness/duplicates/nulls shares over usage_events
  format: score
```

Adding an entry + restarting the agent surfaces it in the metric picker and the decider's `compact_snapshot_summary`. A read-only Metrics viewer (header popover listing name → definition → format) is optional in v2; **no editing UI**.

### 7.6 Paraphrase eval (report-only)

`scripts/eval_paraphrase.py`: ~20 paraphrases per golden path (tense/person/period rewordings, e.g. "how did revenue do last quarter?") + 10 off-catalog distractors, each run on seeded-shape sandbox, random-profile sandbox (`--seed 7/11/23`), under heuristic and under `LIVE=1` live model. Reports per-path component-pair accuracy, unknown-type count (must always be 0), fallback rate, median latency. Output is a markdown table printed + saved to `eval/report.md`; exit code stays 0 on low scores in v2 (signal, not gate).

---

## 8. Technology Stack

| Layer | v2 choice | Notes / change from v1 |
|---|---|---|
| Frontend | React 19 + Vite 7 + TS + Tailwind 4 (same) | Prod `dist/` served via `nginx`/`serve` multi-stage image; dev server stays local-only |
| Chat/wiring | `@copilotkit/react-core 1.79.0`, `@ag-ui/client 1.0.2` (pinned, same) | No new runtime; dataset id travels in snapshot, not protocol |
| UI kit | Existing beUI copies + Recharts (same 6) | +3 small new components (DatasetPicker, MetricPicker, SqlBox); virtualization droppable (50-row cap) |
| Agent | LangGraph 1.2.11 + FastAPI + AG-UI `/copilotkit` (same) | + `/data/*` REST routes; no new graph nodes |
| Decisions | `DecisionClient` + OpenRouter alpha + heuristic (same) | Prompts gain active metric defs; eval covers live quality |
| Semantics | **NEW `metrics.yaml` + loader/compiler** | File edit + restart; validated at load |
| Data | **Sandbox SQLite (stdlib) + Postgres second source** | `psycopg` (read-only role) new dep; no warehouse beyond Postgres in v2 |
| Samples | **NEW `samples/*.csv` <1 MB** | Generator stays the full-data reproducer |
| Infra | docker-compose (agent + web) | No seeder; `./data:/data` keeps sandboxes only; host ports env-driven (same) |

---

## 9. Security & Configuration

- **No auth (v2, unchanged)** — local single-user; network exposure is out of scope. Open-sourcing does not add auth; the README states the local-only posture.
- **Upload hardening:** 25 MB cap, CSV header allowlist (four known tables), type coercion with rejection, one-table-per-upload, sandbox files never executable, `mode=ro` reads everywhere except the ingest write itself.
- **SQL box hardening:** single `SELECT` only (leading-keyword check + blocklist + no semicolons), server-appended `LIMIT 50`, 5 s statement timeout, read-only connection/role on both SQLite (`mode=ro` URI) and Postgres (least-privilege role), errors returned as data never stack traces.
- **Secrets:** `OPENROUTER_API_KEY` server-side only (gitignored `agent/.env`); key never reaches the browser (unchanged). Sample CSVs are synthetic (combo-word names, enum segments/plans, integer cents) — no PII, license/provenance note in `samples/README`.
- **Configuration (additive to v1's 16 vars):**

| Variable | Required | Purpose |
|---|---|---|
| `DB_PATH` | no (default empty = no dataset) | Active sandbox SQLite file; replaces `DEFAULT_DB_PATH`-as-singleton |
| `DATASOURCE_URL` | no (default empty) | Second source URL, e.g. `postgresql://reader:…@host/db`; empty = SQLite sandboxes only |
| `DATASOURCE_RO` | no (default `1`) | `1` forces read-only role/connection; anything else refuses to start |
| `UPLOAD_MAX_MB` | no (default `25`) | Upload size cap |
| `SQL_ROW_LIMIT` | no (default `50`) | Appended limit + table cap (matches catalog `max_length=50`) |
| `SQL_TIMEOUT_S` | no (default `5.0`) | SQL-box statement timeout |

All new vars join `config.py` / `.env.example` / `test_env_contract.py`'s 3-way pin. `SPA_ORIGIN` becomes a comma-separated allowlist (adds `tauri://localhost`, `http://tauri.localhost` for the future desktop webview; behavior-neutral today).

---

## 10. API Specification

### Datasets (new)

```
POST /data/upload (multipart: file=<csv>, table=accounts|subscription_events|revenue_events|usage_events)
→ 201 {id, table, rows, data_end} | 400 {error} (bad header/date/empty/oversize)

GET /data/datasets
→ 200 {active_id, datasets: [{id, source, tables, rows, data_end}]}

POST /data/active {id}
→ 200 {active_id} | 404 {error}

GET /data/run?metric=&start=&end=&segment=
→ 200 A2UILayout (manual composition, "manual" badge client-side) | 400 {error}
POST /data/query {sql}
→ 200 {columns, rows<=50} | 400 {error} (blocked keyword/multi-statement/timeout)
```

### Agent (unchanged wire, rescoped data)

```
POST /copilotkit (AG-UI SSE, existing)
shared state: a2ui, decisions[], status + snapshot.dataset_id (new)
GET /health → exactly {"status": "ok"} (unchanged)
```

### DecisionClient / A2UI (unchanged)

PRD v1 §10 wire formats carry over verbatim: `{"model","state","questions"} → {"answers"}` for decisions; `{v:1, layout:"grid", items, slotCopy}` for layouts. Manual compositions validate through the same schemas.

---

## 11. Success Criteria

**v2 MVP success = empty boot + upload → triple-query parity + samples committable.**

- [ ] `docker compose up` with no dataset: `/health` 200, canvas shows empty state (not charts, not zeros-as-data), chat replies "load a dataset first" and run ends `RUN_FINISHED`
- [ ] Sample load → the three golden questions reproduce on demand: Q3 `kpi_row` + `line_chart` over the sample window; churn `variance_callout` (repricing cause) + cohort table ≤50 rows; DQ card with the sample's dirty week (`duplicates:fail, nulls:fail, freshness:ok`, `data_problem` p>0.7)
- [ ] Same question three ways agrees: NL chat, metric-picker Run, and guarded SQL return mutually consistent numbers on the same sandbox (spot-checked by eval, not bit-identical by construction)
- [ ] Upload isolation: two sandboxes active in sequence never leak rows across (row-count + `data_end` profile line correct per switch; no cross-dataset table reads possible)
- [ ] Postgres parity: golden probes green against `DATASOURCE_URL` Postgres-loaded seed with zero code change
- [ ] Off-catalog still safe: distractors render only `insight_text`; unknown types never render (fallback card); eval reports unknown-type count = 0
- [ ] Paraphrase report exists (`eval/report.md`) with per-path accuracy + fallback rate on seeded + random profiles, heuristic vs live
- [ ] Repo open-source-safe: no committed file >1 MB, `git clone` stays lean, `samples/` + generator reproduce everything, no secrets in image/history
- [ ] Quality gates: `ruff + pytest` (agent) and `lint + typecheck + vitest` (web) clean; updated docker-contract tests reflect no-seed entrypoint; prod web image serves `dist/` (no dev HMR in the image)

**Quality indicators:** upload-to-first-chart <60 s; empty boot <15 s; SQL box p95 <3 s on samples; paraphrase accuracy reported (no gate in v2); dark/light parity kept; desktop-first ≥1280px.

---

## 12. Implementation Phases

### Phase 0 — De-seed (days 1–2)
**Goal:** empty becomes the only honest state.
**Deliverables:**
- [ ] Delete server background seeder + entrypoint sync seed + `RESEED` + compose seed identity (keep `./data` volume for sandboxes)
- [ ] Empty-state canvas + "no dataset" chat short-circuit (`RUN_FINISHED`, empty `decisions[]`, drawer note)
- [ ] Rewrite seeded pins: `verify_demo.py` (drop seed-wait, take `--dataset` target), `test_docker_contract`, `test_generate_data DEFAULT_DB_PATH` pin, golden tests → sample-backed or marked seeded-contract
- [ ] README quickstart becomes upload-first
**Validation:** fresh clone → `compose up` → empty state + 200 health; no `generate_data` process ever runs at boot.

### Phase 1 — Samples + sandbox + upload (days 3–5)
**Goal:** load data on demand into isolation.
**Deliverables:**
- [ ] `samples/*.csv` (<1 MB) + checksums + provenance README; loader recreating the 3 `event_date` indexes
- [ ] `/data/upload|datasets|active` routes + `DB_PATH`-aware `db.py` + dataset picker + empty-state dropzone + "load sample" button
- [ ] `generate_data.py --db-path` documented as reproducer (incl. `--profile random --seed N` for unplanted testing)
**Validation:** upload sample → golden Q3/churn/DQ reproduce; two-sandbox switch shows correct profile lines with zero cross-reads.

### Phase 2 — Source + semantics (days 6–8)
**Goal:** Postgres second source, file-defined metrics.
**Deliverables:**
- [ ] `agent/data/` protocol + `SqliteSource` (verbatim move) + `PostgresSource` (read-only, template-parity) + `DATASOURCE_URL/RO` wiring + contract tests
- [ ] `agent/semantics/metrics.yaml` (6 current + 2 new file-only metrics) + loader/compiler + picker integration + optional read-only viewer
- [ ] `scripts/load_postgres.sh` (seed → Postgres) + parity probe
**Validation:** same suite green on SQLite sandbox and on Postgres; new YAML metric appears in picker after restart with no code edit.

### Phase 3 — Dual-mode minimal web + query runs (days 9–11)
**Goal:** manual BI beside the agent, same renderer, minimal image.
**Deliverables:**
- [ ] Metric picker + Run (`/data/run`), guarded SQL box (`/data/query`), "manual" badge, error-as-`insight_text`
- [ ] Agent rescoped to active dataset (`snapshot.dataset_id`, per-DB `latest_event_day`, DQ scope per sandbox)
- [ ] Multi-stage prod web Dockerfile; devDeps/test/capture out of image; simplified drawer keeps probabilities/model/latency
- [ ] `scripts/eval_paraphrase.py` (report-only) + `eval/report.md` on seeded + random seeds
**Validation:** §11 triple-query parity spot-check; dist image serves with no dev server; eval report committed.

### Phase 4 — Hardening + open-source release (days 12–13)
**Goal:** ship-ready public repo.
**Deliverables:**
- [ ] Full suite green (`ruff/pytest/vitest/eslint/typecheck`), updated contracts, prod compose smoke
- [ ] README rewrite (empty-boot quickstart, sample tour, Postgres/CSV guide, metrics.yaml guide, desktop-note), LICENSE + samples provenance, size audit (`no committed file >1MB`)
- [ ] `verify_data_slice.py` (upload → triple-query → Postgres parity → off-catalog safety) as the v2 §11 executable
**Validation:** clean-clone → `compose up` → `verify_data_slice.py` exit 0; clone size sane.

---

## 13. Future Considerations

- **Desktop port** (the stated horizon): Tauri/Electron shell, agent as local sidecar (`VITE_AGENT_URL` per-launch injection, free-port discovery, sidecar spawn/kill/logs), app-data DB dir replacing `parents[1]/data`, file-picker via IPC instead of multipart upload, offline operation, code signing + updater. v2 prep (multi-origin CORS, `DB_PATH` env, prod `dist/`, `/health` probe) is the runway.
- **Persistent dataset library** (immediate post-v2): cross-restart registry, rename/delete, compare-across-datasets view.
- **More of real BI, in order:** dashboard save/publish/version → filters/slicers + cross-filtering → scheduling/alerts/subscriptions → warehouse beyond Postgres → lineage/governance → auth/RBAC/audit.
- **Eval → gate:** once live-model paraphrase numbers are known, promote the eval to a merge gate (e.g. ≥80% component-pair accuracy) and recalibrate the 0.6 threshold per route.
- **Metrics editor UI** only after file-based semantics prove out; "why this layout?" narration and decision-log CSV export ride along.

---

## 14. Risks & Mitigations

1. **De-seed breaks the entire test/verify chain that pins seeded shapes** (golden Q3/DQ/churn, `wait_for_seed`, docker-contract asserting `generate_data` in entrypoint) → Phase 0 rewrites them to sample-backed or explicit seeded-contract seams first; keep `conftest.py` tmp-DB pattern (already immune to `data/app.db` deletion).
2. **"Same data as CSV" collides with git scale** (full data ~2.9M rows / 138 MB SQLite; GitHub 100 MB/file limit; clone bloat) → tiny `samples/` (<1 MB) committed + generator + release-asset for full CSVs; size audit in Phase 4 gates the release.
3. **Manual and agent modes diverge into two renderers** (the v1 control-layer guarantee dies) → both validate through the same `catalog.A2UILayout` + Zod mirror + registry; manual compositions carry no `decisions[]` and a "manual" badge so provenance is visible, never a second component set.
4. **Postgres parity drifts from SQLite** (dialect, date functions, read-only roles) → same static-template discipline, protocol-level contract tests running every query on both backends, one `load_postgres.sh` reproducer.
5. **Upload/SQL box becomes an injection or DoS vector** (even local) → header allowlist + single-`SELECT` gate + blocklist + appended `LIMIT` + timeouts + read-only connections; errors as data, never traces; caps in env with safe defaults.

---

## 15. Appendix

- **Superseded spec:** `.agents/PRDs/PRD.md` v1.1 (seeded Jev demo, 6-component catalog, 5-node graph, OpenRouter alpha decisions, 3 golden paths) — mechanism design carries over; seeding assumptions do not.
- **Key carry-over contracts:** A2UI `{v:1, layout:"grid", items, slotCopy}` (v1 §10); decisions `{"model","state","questions"} → {"answers"}`; threshold 0.6 inclusive + `insight_text` fallback; Investigate `[investigate] churn cohort` prefix (graph ↔ `useInvestigate` verbatim twin); 50-row table cap.
- **New files:** `samples/*`, `agent/data/*`, `agent/semantics/metrics.yaml`, `web/.../DatasetPicker|MetricPicker|SqlBox|EmptyState`, `scripts/eval_paraphrase.py`, `scripts/verify_data_slice.py`, `scripts/load_postgres.sh`.
- **What "YAML metrics" means (glossary):** metric definitions live in a config file (`name/definition/grain/format/owner`), edited in any text editor; the agent compiles them to SQL at startup. v2 has no metrics UI editor — file edit + agent restart is the workflow; the optional header viewer is read-only documentation.
- **What "sandbox" means (glossary):** one uploaded/sample dataset materialized as one isolated database file (or Postgres schema) that queries can never escape; switching datasets switches the entire queryable world; v2 sandboxes are throwaway.
- **Reply to "can web be a BI tool?":** yes — Metabase/Superset/Tableau Cloud/PowerBI Service prove it. Web is kept minimal in v2 by strategy (prove the pattern + dual-mode parity), not by platform limitation; desktop later buys local files + offline + privacy, not legitimacy.

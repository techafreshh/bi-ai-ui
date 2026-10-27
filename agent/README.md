# bi-ai-ui agent

LangGraph agent serving the AG-UI `/copilotkit` endpoint (see `server.py`).

Mock data: `python scripts/generate_data.py` builds the deterministic seeded
SQLite database at `../data/app.db` (gitignored) — schema documented in the
script's docstring, connection helper in `db.py`. `server.py` also seeds it
itself in a daemon thread at boot when the file is absent (a runtime
checkout ships without it); `/health` never waits on the seed, and the
graph degrades to placeholder copy while it runs.

## Agent graph (issue #10)

`graph.py` wires five nodes — `read_state → decide → layout → query →
fill` — over a shared `AgentState` whose `a2ui` / `decisions[]` / `status`
fields stream to the client as AG-UI STATE_SNAPSHOTs on every node exit:

* `read_state` resolves the query's display window (`questions.py` —
  regex-based quarters/months anchored on the data's latest event day) and
  loads window-scoped KPIs + previous-quarter comparison, the default-scope
  data-quality checks and the churn delta (`queries.py`, `quality.py`).
* `decide` fans the fixed catalog question set (one choice over the six
  component types, two noul, one score — `questions.py`) through
  `DecisionClient.decide`, stamping each streamed entry with `id`, `type`,
  `probabilities`, `pick`, `model` and per-call `latency_ms`. Transport
  failures degrade to a fresh heuristic run, so `decisions` always carries
  1..7 entries. Off-catalog queries (no data domain) are answered by the
  deterministic guard transport without consulting the wired model (issue
  #18), so their threshold cut empties into the insight fallback.
* `layout` builds a complete draft of every catalog component from the
  snapshot, cuts it with `catalog.filter_layout` at DECISION_THRESHOLD and
  parse-asserts the result through `A2UILayout`; nothing surviving falls
  back to the single-insight_text default view.
* `query` re-runs window-scoped SQL for the three SQL-backed slots (DQ
  checks, churn cohort rows, churn variance) and re-validates the layout.
* `fill` makes exactly one streamed text-model call (OpenRouter chat via
  `build_chat_model`) asking for `kpis.title` / `insight_text.body` /
  `summary` copy, routes it into `a2ui.slotCopy`, and closes the run with
  the summary as the chat reply — degrading to KPI-grounded deterministic
  copy when no key is set or the reply is unparseable.

## Decisions (issues #8/#9)

`build_decision_client()` wires the decision seam to OpenRouter: a
`DecisionsModalityTransport` POSTs the PRD §10 batch wire format
(`{"model", "state", "questions"}` → `{"answers": [...]}`) to
`{OPENROUTER_BASE_URL}/decisions` for `DECISION_MODEL` (default
`~perplexity/pplx-decider-flash-latest`). On failure or non-200, the
`FallbackChainTransport` engages `ChatStructuredTransport`, which walks
`DECISION_FALLBACK_MODELS` (default `inception/mercury-decide:free,
openai/gpt-6-luna`) over chat completions with `response_format:
json_schema` asking for the same answer shape. Each transport gets at most
`MAX_ATTEMPTS_PER_TRANSPORT` (2) attempts; when every transport fails,
`decide()` raises `TransportError` — bounded, no infinite retries. Without
`OPENROUTER_API_KEY` the factory raises `RuntimeError` at construction
(message mirrors the chat model's). Per-request timeouts come from
`DECISION_TIMEOUT_S` (default 8.0).

The decide node (issue #10) wires this chain through `get_decision_client`
when a key is set, and wraps whatever transport it gets in a wall-clock
recorder so each decision entry can carry its latency. Any transport
failure at run time — or a missing key at wiring time — falls back to
`decision/heuristic.py`'s `HeuristicTransport`: a key-free, deterministic
keyword classifier over the same per-question seam that endorses exactly
two components per golden query (sales → kpi_row + line_chart, churn →
variance_callout + data_table, data quality → data_quality_card) and
nothing for off-catalog queries.

Capture or refresh the live wire contract with the spike — it records the
raw decisions response to `tests/contract_snapshot.json`
(`{"meta", "response"}`), which `DecisionsModalityTransport.parse` and the
snapshot pin test consume:

    python agent/scripts/contract_spike.py                  # decisions only
    python agent/scripts/contract_spike.py --transport both # probe the chat
                                                            # fallback too

## Tests (issue #20)

The whole suite runs from the repo root with no API key and no network —
transports are mocked (`httpx.MockTransport`), the seeded DB comes from
one generator subprocess shared through `tests/conftest.py`, and the only
servers touched bind loopback:

    pytest agent/tests -q            # or: uv run --project agent pytest agent/tests -q

Exit code 0, roughly a minute. Layout validator (`test_catalog.py`),
DecisionClient parsing (`test_decision_client.py`), transports and timeout
fallback (`test_decision_transports.py`), DQ checks (buggy vs clean
fixtures, `test_quality.py`), and the graph/SSE pipelines all run; the live
probe in `test_live_decision.py` reports as skipped. Select a suite with
`-k` (`pytest agent/tests -k quality -q`).

The live decision probe is opt-in — the same two spike questions against
the real OpenRouter chain (needs network and a key; without a key it fails
loud instead of skipping silently):

    LIVE=1 OPENROUTER_API_KEY=... pytest agent/tests -q -k live_decision

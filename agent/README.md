# bi-ai-ui agent

LangGraph agent serving the AG-UI `/copilotkit` endpoint (see `server.py`).

Mock data: `python scripts/generate_data.py` builds the deterministic seeded
SQLite database at `../data/app.db` (gitignored) — schema documented in the
script's docstring, connection helper in `db.py`.

## Decisions (issue #9)

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

Capture or refresh the live wire contract with the spike — it records the
raw decisions response to `tests/contract_snapshot.json`
(`{"meta", "response"}`), which `DecisionsModalityTransport.parse` and the
snapshot pin test consume:

    python agent/scripts/contract_spike.py                  # decisions only
    python agent/scripts/contract_spike.py --transport both # probe the chat
                                                            # fallback too

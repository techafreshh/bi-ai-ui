# bi-ai-ui

AI business-intelligence dashboard where a chat agent answers natural-language
queries by **composing the UI from a fixed catalog of pre-built components** —
the "Jev generative UI" pattern. This repository currently contains the
**walking skeleton** (issue #1): a Vite React SPA, a LangGraph FastAPI agent,
and a live chat round-trip.

## Quickstart

```bash
cp .env.example agent/.env   # then fill in OPENROUTER_API_KEY
docker compose up
```

- SPA: <http://localhost:5173>
- Agent health: <http://localhost:8123/health> → `{"status": "ok"}`
- Agent AG-UI endpoint: <http://localhost:8123/copilotkit>

Type a message (e.g. `ping`) in the chat sidebar; the streamed reply comes from
the OpenRouter text model — round-trip `browser → HttpAgent → POST /copilotkit
→ LangGraph → OpenRouter`.

Without `OPENROUTER_API_KEY` the agent still boots and serves `/health`; it
logs the missing-key error at startup and chat requests fail until a key is
provided.

## Environment variables (`agent/.env`, server-side only, gitignored)

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | — (required for chat) | OpenRouter API key |
| `TEXT_MODEL` | `openai/gpt-6-luna` | OpenRouter model id for chat replies |

`.env.example` at the repo root is the template; copy it to `agent/.env`.

## Ports

Port `8123` for the agent is pinned per the CopilotKit LangGraph quickstart;
`5173` is the Vite default. Both are fixed in `docker-compose.yml` and the
respective configs.

## Architecture (skeleton)

```
React SPA (Vite, :5173)                    LangGraph agent (FastAPI, :8123)
CopilotKitProvider (v2) ─── HttpAgent ──▶ POST /copilotkit (AG-UI SSE)
  CopilotSidebar         ◀── text deltas ── single-node graph → ChatOpenAI
                                              (OpenRouter, OpenAI-compatible)
```

- `web/` — Vite + TypeScript + Tailwind v4 SPA. The v2 `CopilotKitProvider`
  registers the agent directly via `selfManagedAgents` (no separate Node
  runtime); `@ag-ui/client`'s `HttpAgent` POSTs a `RunAgentInput` straight to
  the agent.
- `agent/graph.py` — one `chat` node; streams the model so its tokens surface
  as AG-UI `TEXT_MESSAGE_*` events; explicit `MemorySaver` (no langgraph-cli
  checkpointer exists under uvicorn + ag-ui-langgraph).
- `agent/server.py` — FastAPI + CORS (allows `http://localhost:5173`),
  `GET /health`, and the `/copilotkit` endpoint via
  `ag_ui_langgraph.add_langgraph_fastapi_endpoint`.
- `agent/tests/` — pytest contract tests (`/health` body, CORS preflight,
  missing-key errors); run with `uv run pytest` in `agent/`.

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
- **`agent/requirements.txt`** — full pinned freeze of `uv.lock` for pip-based
  environments (`pip install -r requirements.txt`); regenerate it with the
  `uv export` command in its header whenever `uv.lock` changes.

## Development without Docker

```bash
# web
cd web && npm install && npm run dev        # :5173

# agent (needs agent/.env with OPENROUTER_API_KEY for chat)
cd agent && uv sync && uv run uvicorn server:app --port 8123

# agent without uv: pip-based equivalent, same pins as uv.lock
python3 -m venv agent/.venv && agent/.venv/bin/pip install -r agent/requirements.txt
agent/.venv/bin/uvicorn server:app --app-dir agent --port 8123
```

The SPA expects the agent at `http://localhost:8123/copilotkit`
(`web/src/App.tsx`).

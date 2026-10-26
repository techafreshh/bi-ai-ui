# bi-ai-ui

AI business-intelligence dashboard where a chat agent composes the UI from a
fixed catalog of pre-built components — see
[`.agents/PRDs/PRD.md`](.agents/PRDs/PRD.md) for the full product spec.

This is the **walking skeleton** (issue #1): a Vite React SPA talking to a
LangGraph Python agent with a live streamed chat round-trip.

## Run

```bash
git clone <repo> && cd bi-ai-ui
cp .env.example agent/.env      # then put your OpenRouter key in agent/.env
docker compose up
```

- SPA: http://localhost:5173 — type "ping" in the sidebar chat and watch the
  streamed reply arrive (round-trip: browser → CopilotKit → agent → OpenRouter
  text model).
- Agent: http://localhost:8123 — `GET /health` returns `{"status": "ok"}`,
  `POST /copilotkit` is the AG-UI/SSE chat endpoint.

Without a key the agent still boots and `/health` stays 200; it logs a clear
error naming `OPENROUTER_API_KEY` and only chat requests fail.

## Ports

| Service | Port | Why |
|---|---|---|
| web (Vite dev server) | 5173 | Vite default |
| agent (FastAPI) | 8123 | Pinned per the CopilotKit LangGraph quickstart |

## Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | `agent/.env` (server-side only, gitignored) | OpenRouter key for the text model |
| `TEXT_MODEL` | `agent/.env` | Model id, default `openai/gpt-6-luna` |
| `OPENROUTER_BASE_URL` | `agent/.env` (optional) | Override to point at another OpenAI-compatible endpoint; default `https://openrouter.ai/api/v1` |

## Dependency pins worth knowing

- **zod v3** — the CopilotKit quickstart pins zod v3; unversioned installs pull
  zod v4, which does not satisfy the CopilotKit SDK peers. We pin the v3 line
  (`^3.25.76`) in `web/package.json`.
- **`@ag-ui/*` at 1.0.2** — npm `overrides` hold the AG-UI package family at
  the version the pinned `@copilotkit/react-core` was built against.
- **`ag-ui-langgraph[fastapi]==0.0.44`** — 0.0.43 breaks `copilotkit==0.1.96`
  at request-cloning time; the reason is documented inline in
  `agent/pyproject.toml`, matching the official starter.

## Development without Docker

```bash
# agent (Python 3.12+)
cd agent && uv sync && uv run python server.py

# web (Node 22+)
cd web && npm install && npm run dev
```

Agent unit tests: `cd agent && uv run pytest -q`.

## Architecture (this slice)

```
React SPA (Vite) :5173          LangGraph agent (FastAPI) :8123
CopilotKitProvider ───────────▶ POST /copilotkit   (AG-UI SSE)
  selfManagedAgents:              LangGraphAGUIAgent bi_agent
  HttpAgent ──────────────────    single-node graph, streams
CopilotSidebar (v2 chat)          TEXT_MESSAGE_* deltas
                                  ChatOpenAI → OpenRouter
```

The SPA connects **directly** to the agent — no Node runtime in between
(PRD v1.1) — which is why the agent's CORS config allows
`http://localhost:5173`.

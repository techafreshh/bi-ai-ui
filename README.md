# bi-ai-ui

AI BI dashboard with a generative-UI chat: a React (Vite) SPA talking directly
to a LangGraph FastAPI agent over the CopilotKit/AG-UI protocol.

## Run it

```bash
git clone <this repo> && cd bi-ai-ui
cp .env.example agent/.env        # then paste your OpenRouter key
docker compose up
```

- SPA: http://localhost:5173
- Agent health: http://localhost:8123/health → `{"status": "ok"}`
- Agent chat endpoint: http://localhost:8123/copilotkit

Type a message in the chat sidebar; the reply streams from the OpenRouter text
model via the agent (round-trip: browser → CopilotKit/AG-UI → LangGraph →
OpenRouter).

## Packaging note: `web/dist` is committed

The built SPA bundle (`web/dist/`) is committed on purpose: the Docker web
image is a thin nginx static server, so a fresh `docker compose up` only pulls
small base images and never runs npm/node — this keeps the one-command boot
to seconds even on slow networks. After editing anything under `web/src`,
regenerate and commit the bundle:

```bash
cd web && npm install && npm run build && git add dist
```

For iterative development use the dev server instead (hot reload, no rebuild
needed):

```bash
cd web && npm install && npm run dev   # Vite dev server on http://localhost:5173
```

## Without Docker

```bash
# agent (Python 3.12+, uv)
cd agent && uv sync && OPENROUTER_API_KEY=<key> uv run uvicorn server:app --port 8123

# web (Node 22+)
cd web && npm ci && npm run dev
```

`agent/.env` is loaded automatically by the agent (`python-dotenv`).

## Configuration

| Variable | Where | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | `agent/.env` (server-side only, gitignored) | OpenRouter API key. Without it the agent boots, logs a clear error, and still serves `/health`; chat fails until a key is set. |
| `TEXT_MODEL` | `agent/.env` | OpenRouter chat model id (default `openai/gpt-6-luna`). |

## Architecture notes

- **Port 8123** for the agent is pinned per the CopilotKit LangGraph quickstart;
  the SPA's `CopilotKitProvider` points at `http://localhost:8123/copilotkit`
  directly from the browser (no separate Node CopilotKit runtime). The FastAPI
  `CORSMiddleware` allows exactly the Vite dev origin `http://localhost:5173`.
- **zod v3 pin**: the CopilotKit quickstart pins zod v3 for SDK compatibility;
  we pin the v3 line (`^3.25.76`) — unversioned installs pull zod v4, which does
  not satisfy the CopilotKit SDK peers.
- **Dependency lockfiles are committed** (`web/package-lock.json`,
  `agent/uv.lock`) for reproducible installs.

## Tests

```bash
docker compose run --rm agent uv run --no-sync pytest -q   # agent contract tests
cd web && npm run build                                    # typecheck + bundle (refresh web/dist)
```

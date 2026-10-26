# bi-ai-ui agent

LangGraph agent serving the AG-UI `/copilotkit` endpoint (see `server.py`).

Mock data: `python scripts/generate_data.py` builds the deterministic seeded
SQLite database at `../data/app.db` (gitignored) — schema documented in the
script's docstring, connection helper in `db.py`.

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
WORKDIR /app
# Dependency files first so the layer caches across code-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
# Demo data layer + five-node graph modules.
COPY store.py queries.py quality.py routes.py catalog.py graph.py server.py ./
COPY decision ./decision
COPY scripts ./scripts
COPY tests ./tests
EXPOSE 8123
CMD ["uv", "run", "--no-sync", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8123"]

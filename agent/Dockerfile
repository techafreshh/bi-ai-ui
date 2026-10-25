FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
WORKDIR /app
# Dependency files first so the layer caches across code-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY server.py graph.py ./
COPY tests ./tests
EXPOSE 8123
CMD ["uv", "run", "--no-sync", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8123"]

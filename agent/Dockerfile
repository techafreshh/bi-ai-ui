FROM python:3.12-slim

# uv pinned per the official langgraph-fastapi starter's image hygiene.
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .

# Seed entrypoint (issue #21): synchronous generate_data.py run when
# data/app.db is absent or RESEED=1, then execs CMD. No secrets here.
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8123

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uv", "run", "--no-sync", "python", "server.py"]

"""Compose hardening contract (issue #21): clean-clone boot and seed reset.

Static, stdlib-only checks over the committed files — no daemon, no DB, no
network — so they run anywhere `pytest agent/tests` runs:

- `docker-compose.yml` mounts the shared `./data` volume at the exact path
  `agent/db.py:DEFAULT_DB_PATH` resolves to in-container (`/data/app.db`),
  defines healthchecks on both services, gates `web` on the agent's health,
  restarts unless-stopped, and passes `RESEED` through without pinning any
  secret value.
- neither Dockerfile bakes a secret (`ARG`/`ENV` key lines), and compose
  carries no key literal either.
- `agent/docker-entrypoint.sh` (the synchronous seed) and
  `scripts/reset_data.sh` (the deterministic reset) exist, are executable,
  and call the generator's `--db-path` interface rather than raw SQL.
- `.gitignore` still keeps `.env` and the generated `data/` out of git.
"""

import re
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
AGENT_DOCKERFILE = (AGENT_DIR / "Dockerfile").read_text(encoding="utf-8")
WEB_DOCKERFILE = (REPO_ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
GITIGNORE = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_compose_defines_healthchecks_on_both_services():
    assert COMPOSE.count("healthcheck:") >= 2, (
        "agent and web must each define a healthcheck"
    )


def test_web_waits_for_healthy_agent():
    assert "service_healthy" in COMPOSE, (
        "web.depends_on.agent must use condition: service_healthy"
    )


def test_shared_data_volume_matches_container_db_path():
    # agent/db.py:DEFAULT_DB_PATH is parents[1]/data/app.db; in-container
    # db.py lives at /app/db.py (WORKDIR /app), hence /data/app.db.
    assert "./data:/data" in COMPOSE, (
        "agent needs a ./data:/data bind mount so the seed lands on the "
        "host's repo-root data/app.db"
    )


def test_restart_policies_and_reseed_passthrough():
    assert COMPOSE.count("unless-stopped") >= 2
    assert "RESEED" in COMPOSE, "compose must pass RESEED through (--reseed)"


def test_no_image_build_embeds_any_secret():
    assert "OPENROUTER_API_KEY" not in COMPOSE
    for name, text in (("agent", AGENT_DOCKERFILE), ("web", WEB_DOCKERFILE)):
        assert "OPENROUTER_API_KEY" not in text, name
        for line in text.splitlines():
            assert not re.match(
                r"\s*(ARG|ENV)\s+\w*(KEY|SECRET|TOKEN|PASSWORD)\b", line
            ), f"{name}/Dockerfile bakes a secret: {line.strip()}"


def test_agent_entrypoint_seed_script():
    entrypoint = AGENT_DIR / "docker-entrypoint.sh"
    assert entrypoint.is_file()
    assert entrypoint.stat().st_mode & stat.S_IXUSR, "must be executable"
    text = entrypoint.read_text(encoding="utf-8")
    assert "scripts/generate_data.py" in text
    assert "--db-path" in text, "must use the generator's --db-path interface"
    assert "/data/app.db" in text, "must target the mounted container DB path"
    assert text.rstrip().endswith('exec "$@"'), "must exec the server command"


def test_reset_script_uses_built_image_generator():
    reset = REPO_ROOT / "scripts" / "reset_data.sh"
    assert reset.is_file()
    assert reset.stat().st_mode & stat.S_IXUSR, "must be executable"
    text = reset.read_text(encoding="utf-8")
    assert "docker compose run" in text, "must regen through the agent image"
    assert "scripts/generate_data.py" in text
    assert "--db-path" in text, "must use the generator's --db-path interface"


def test_gitignore_keeps_secrets_and_generated_db_out():
    assert re.search(r"^\.env$", GITIGNORE, re.MULTILINE), (
        ".env must be listed in .gitignore"
    )
    assert "data/" in GITIGNORE

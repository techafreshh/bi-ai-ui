"""Compose hardening contract (issues #21, #70, #71, #91): clean-clone boot,
DB-less, open-source safety.

Static, stdlib-only checks over the committed files — no daemon, no DB, no
network — so they run anywhere `pytest agent/tests` runs:

- `docker-compose.yml` mounts the shared `./data` volume at the exact path
  `agent/db.py:DEFAULT_DB_PATH` resolves to in-container (`/data/app.db`),
  defines healthchecks on both services, gates `web` on the agent's health,
  restarts unless-stopped — and carries NO seed identity: no `RESEED`, no
  generator invocation, so boot never creates a database (issue #70).
- neither Dockerfile bakes a secret (`ARG`/`ENV` key lines), and compose
  carries no key literal either.
- `agent/docker-entrypoint.sh` exists, is executable, execs the server
  command, and performs no seeding; `scripts/reset_data.sh` (the manual
  deterministic reset) exists, is executable, and calls the generator's
  `--db-path` interface rather than raw SQL.
- `.gitignore` still keeps `.env` and the generated `data/` out of git.
- issue #91 release hardening: no committed file over 1MB (tracked files
  only, so local build artifacts never trip the gate), neither Dockerfile
  carries a secret shape or a `.env` COPY, and the slice verifier exists,
  is executable, and names its contract strings.
"""

import re
import stat
import subprocess
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


def test_restart_policies_without_reseed():
    assert COMPOSE.count("unless-stopped") >= 2
    assert "RESEED" not in COMPOSE, (
        "issue #70: boot is DB-less — compose must not reseed at boot"
    )
    assert "generate_data" not in COMPOSE, (
        "issue #71: compose must not invoke the generator at boot"
    )
    assert "wait_for_seed" not in COMPOSE, (
        "issue #71: compose must not seed-wait at boot"
    )
    assert "--db-path" not in COMPOSE, (
        "issue #70: compose must not invoke the generator at boot"
    )


def test_no_image_build_embeds_any_secret():
    assert "OPENROUTER_API_KEY" not in COMPOSE
    for name, text in (("agent", AGENT_DOCKERFILE), ("web", WEB_DOCKERFILE)):
        assert "OPENROUTER_API_KEY" not in text, name
        for line in text.splitlines():
            assert not re.match(
                r"\s*(ARG|ENV)\s+\w*(KEY|SECRET|TOKEN|PASSWORD)\b", line
            ), f"{name}/Dockerfile bakes a secret: {line.strip()}"


def test_agent_entrypoint_boots_db_less():
    entrypoint = AGENT_DIR / "docker-entrypoint.sh"
    assert entrypoint.is_file()
    assert entrypoint.stat().st_mode & stat.S_IXUSR, "must be executable"
    text = entrypoint.read_text(encoding="utf-8")
    assert "generate_data" not in text, "must not invoke the generator at boot"
    assert "wait_for_seed" not in text, "must not seed-wait at boot"
    assert "--db-path" not in text, "must not invoke the generator at boot"
    assert "RESEED" not in text, "must not reseed at boot"
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


def test_no_committed_file_over_1mb():
    # Tracked files only (`git ls-files`): local build artifacts
    # (node_modules, .venv) are untracked and must never trip this gate —
    # the issue's `find` clause runs on a clean clone where they are absent.
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=60,
    )
    assert listed.returncode == 0, listed.stderr.decode()[:200]
    oversized = []
    for name in listed.stdout.decode().split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 1048576:
            oversized.append(f"{name} ({size} bytes)")
    assert oversized == [], f"committed files over 1MB: {oversized}"


def test_no_image_build_embeds_any_secret_shape():
    # The open-source image must carry no secret shape at all: no key
    # literal, no ARG/ENV *KEY|SECRET|TOKEN|PASSWORD* line, and no `.env`
    # COPY — `.env` rides the compose env_file passthrough (gitignored),
    # never the image.
    for name, text in (("agent", AGENT_DOCKERFILE), ("web", WEB_DOCKERFILE)):
        assert "OPENROUTER_API_KEY" not in text, name
        for line in text.splitlines():
            assert not re.match(
                r"\s*(ARG|ENV)\s+\w*(KEY|SECRET|TOKEN|PASSWORD)\b", line
            ), f"{name}/Dockerfile bakes a secret: {line.strip()}"
            stripped = line.strip()
            if stripped.startswith("COPY") and ".env" in stripped:
                # `.env.example` is the documented template; a real `.env`
                # (bare, or a dotted variant) must never be baked in.
                assert ".env.example" in stripped, (
                    f"{name}/Dockerfile copies an env file: {stripped}"
                )
        assert not re.search(r"(?m)^\s*COPY\s+.*\b\.env\b(?!\.example)", text), (
            f"{name}/Dockerfile copies a real .env file"
        )


def test_slice_verifier_script_contract():
    # The release gate is one executable file naming its contract strings:
    # the `--target` flag, both query legs, the insight fallback type and
    # the exact success line the acceptance greps for.
    script = REPO_ROOT / "scripts" / "verify_data_slice.py"
    assert script.is_file(), "scripts/verify_data_slice.py must exist"
    assert script.stat().st_mode & stat.S_IXUSR, "must be executable"
    text = script.read_text(encoding="utf-8")
    for needle in (
        "parity: ok",
        "--target",
        "/data/query",
        "/data/run",
        "insight_text",
    ):
        assert needle in text, f"verifier must name {needle!r}"

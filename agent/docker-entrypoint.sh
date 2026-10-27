#!/bin/sh
# Synchronous seed entrypoint for the agent container (issue #21).
# Mirrors agent/server.py:_ensure_seed_database (skip when the DB exists,
# pid-suffixed tmp file + atomic move so a killed generator never leaves a
# partial live database), but runs synchronously so a clean-clone
# `docker compose up` has data/app.db before the server accepts traffic.
# The background self-seed in server.py stays untouched as the fallback.
set -eu

# agent/db.py:DEFAULT_DB_PATH resolves from the file location, never the
# cwd: Path(db.py).parents[1]/data/app.db. In this image db.py lives at
# /app/db.py (WORKDIR /app), so the in-container path is /data/app.db —
# the ./data:/data bind mount in docker-compose.yml lands the seed on the
# host's repo-root data/app.db.
DB_PATH="${DB_PATH:-/data/app.db}"
# RESEED=1 forces regeneration even when the DB exists (--reseed); the
# compose default is 0 so normal boot only seeds when absent.
RESEED="${RESEED:-0}"

if [ "$RESEED" = "1" ] || [ ! -f "$DB_PATH" ]; then
  mkdir -p "$(dirname "$DB_PATH")"
  TMP_PATH="${DB_PATH}.$$.seed-tmp"
  rm -f "$TMP_PATH"
  python3 scripts/generate_data.py --db-path "$TMP_PATH"
  mv -f "$TMP_PATH" "$DB_PATH"
fi

exec "$@"

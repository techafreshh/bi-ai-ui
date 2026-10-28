#!/bin/sh
# Postgres sample loader for the read-only second source (issue #80).
# Creates the four event/account tables plus the three `event_date` indexes
# (column order verbatim from agent/sandbox.py:SANDBOX_SCHEMA_SQL — TEXT
# ISO days, integer cents, so the same BETWEEN comparisons the SQLite path
# uses hold here), then `\copy`s the repo-root `samples/*.csv` files
# (CSV HEADER, empty fields read as NULL for the nullable `units` column),
# then grants a least-privilege `reader` role SELECT-only access with
# `default_transaction_read_only=on`. The app itself never runs DDL —
# only this script does. Run from the repo root:
#   DATASOURCE_URL=postgresql://user:pass@localhost:5432/bi scripts/load_postgres.sh
# Reruns are idempotent: tables are TRUNCATEd (never dropped) before each
# copy, so row counts always equal the four samples/*.csv data-row counts.
# Only `--reset` drops the tables first (full rebuild, grants re-applied).
set -eu

# Resolve the repo root from this script's location, never the cwd
# (agent/db.py precedent: DEFAULT_DB_PATH resolves from file location).
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

RESET=0
while [ "$#" -gt 0 ]; do
  arg="$1"
  case "$arg" in
    --reset) RESET=1 ;;
    -h|--help)
      echo "usage: DATASOURCE_URL=postgresql://user:pass@host:port/db scripts/load_postgres.sh [--reset]"
      echo "  loads samples/*.csv into Postgres for the read-only second source (issue #80)"
      echo "  --reset drops the four tables before recreating them; without it they are"
      echo "  created if missing and TRUNCATEd before each copy (never dropped)"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (usage: DATASOURCE_URL=... scripts/load_postgres.sh [--reset])" >&2
      exit 1
      ;;
  esac
  shift
done

if [ -z "${DATASOURCE_URL:-}" ]; then
  echo "error: DATASOURCE_URL is not set (e.g. postgresql://user:pass@localhost:5432/bi)" >&2
  exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
  echo "error: psql is not on PATH" >&2
  exit 1
fi
for table in accounts subscription_events revenue_events usage_events; do
  if [ ! -f "samples/$table.csv" ]; then
    echo "error: samples/$table.csv not found (run from the repo root)" >&2
    exit 1
  fi
done

# Verbatim from agent/sandbox.py:SANDBOX_SCHEMA_SQL (the single source of
# truth for table shape): the loader always builds all of it even though
# each copy imports only one CSV table, so Postgres exposes the same
# indexes the SQLite acceptance probe queries. TEXT ISO days keep the
# BETWEEN comparisons identical across backends.
if [ "$RESET" -eq 1 ]; then
  psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 -c \
    "DROP TABLE IF EXISTS usage_events, revenue_events, subscription_events, accounts;"
fi
psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT NOT NULL,
    plan TEXT NOT NULL,
    arr_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscription_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    arr_delta_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS revenue_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    event_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    event_date TEXT NOT NULL,
    metric TEXT NOT NULL,
    units INTEGER
);
CREATE INDEX IF NOT EXISTS idx_subscription_events_event_date ON subscription_events(event_date);
CREATE INDEX IF NOT EXISTS idx_revenue_events_event_date ON revenue_events(event_date);
CREATE INDEX IF NOT EXISTS idx_usage_events_event_date ON usage_events(event_date);
SQL

# Empty-before-copy (TRUNCATE, never DROP without --reset) so reruns land
# the exact CSV row counts instead of appending duplicates; RESTART IDENTITY
# is harmless (ids ride the CSV) and keeps sequences, if any, consistent.
psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 -c \
  "TRUNCATE accounts, subscription_events, revenue_events, usage_events RESTART IDENTITY;"

for table in accounts subscription_events revenue_events usage_events; do
  # CSV HEADER skips the header row; NULL '' maps the empty `units` field
  # to NULL exactly like the sandbox loader's int-or-None coercion.
  psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 -c \
    "\\copy $table FROM '$ROOT/samples/$table.csv' WITH (FORMAT csv, HEADER true, NULL '')"
done

# Least-privilege reader role (issue #80): login allowed, no superuser /
# createdb / createrole, SELECT on the four tables only, read-only by
# default. Guarded by a DO block so reruns never fail on an existing role.
psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'reader') THEN
    CREATE ROLE reader NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION LOGIN;
  END IF;
END
$$;
GRANT USAGE ON SCHEMA public TO reader;
GRANT SELECT ON accounts, subscription_events, revenue_events, usage_events TO reader;
ALTER ROLE reader SET default_transaction_read_only = on;
SQL
# CONNECT rides the database named in DATASOURCE_URL (PUBLIC already holds
# it by default; this makes the grant explicit for hardened clusters).
psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 -c \
  "SELECT format('GRANT CONNECT ON DATABASE %I TO reader', current_database())" -t -A | \
  while IFS= read -r grant; do
    [ -z "$grant" ] || psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 -c "$grant"
  done

psql "$DATASOURCE_URL" -v ON_ERROR_STOP=1 -c \
  "SELECT 'accounts=' || (SELECT COUNT(*) FROM accounts) || \
   ' subscription_events=' || (SELECT COUNT(*) FROM subscription_events) || \
   ' revenue_events=' || (SELECT COUNT(*) FROM revenue_events) || \
   ' usage_events=' || (SELECT COUNT(*) FROM usage_events) || \
   ' latest_event_day=' || (SELECT MAX(d) FROM (SELECT MAX(event_date) AS d FROM subscription_events UNION ALL SELECT MAX(event_date) FROM revenue_events UNION ALL SELECT MAX(event_date) FROM usage_events) AS latest);"

#!/bin/sh
# Deterministic DB reset via the already-built agent image (issue #21).
# Regenerates data/app.db through scripts/generate_data.py's --db-path
# interface (never raw SQL), so reruns yield identical row counts
# (SEED determinism). Host Python is never required. Run from the repo
# root; may briefly stop the stack. Usage:
#   ./scripts/reset_data.sh [--restart]
set -eu

# Resolve the repo root from this script's location, never the cwd
# (agent/db.py precedent: DEFAULT_DB_PATH resolves from file location).
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

# In-container seed path (agent/db.py:DEFAULT_DB_PATH under WORKDIR /app
# is /data/app.db, bind-mounted to the host's repo-root ./data).
DB_MOUNT_PATH="/data/app.db"

RESTART=0
# Dash-safe (no arrays in POSIX sh): profile flags accumulate as a
# word-split string of "flag value" pairs. Values are seed/profile tokens
# from the user's own invocation. while+shift consumes flag+value pairs;
# the bottom shift consumes each head token.
PROFILE_ARGS=""
while [ "$#" -gt 0 ]; do
  arg="$1"
  case "$arg" in
    --restart) RESTART=1 ;;
    --profile|--seed)
      if [ "$#" -lt 2 ]; then
        echo "missing value for $arg" >&2
        exit 1
      fi
      PROFILE_ARGS="$PROFILE_ARGS $arg $2"
      shift
      ;;
    -h|--help)
      echo "usage: scripts/reset_data.sh [--restart] [--profile seeded|random] [--seed N]"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (usage: scripts/reset_data.sh [--restart] [--profile seeded|random] [--seed N])" >&2
      exit 1
      ;;
  esac
  shift
done

mkdir -p data

if [ "$RESTART" -eq 1 ]; then
  docker compose stop agent >/dev/null 2>&1 || true
fi

docker compose run --rm agent python scripts/generate_data.py \
  --db-path "$DB_MOUNT_PATH" "${PROFILE_ARGS[@]}"

if [ "$RESTART" -eq 1 ]; then
  docker compose start agent >/dev/null 2>&1 || docker compose up -d agent
fi

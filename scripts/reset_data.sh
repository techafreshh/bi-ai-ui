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
for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=1 ;;
    -h|--help)
      echo "usage: scripts/reset_data.sh [--restart]"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (usage: scripts/reset_data.sh [--restart])" >&2
      exit 1
      ;;
  esac
done

mkdir -p data

if [ "$RESTART" -eq 1 ]; then
  docker compose stop agent >/dev/null 2>&1 || true
fi

docker compose run --rm agent python scripts/generate_data.py --db-path "$DB_MOUNT_PATH"

if [ "$RESTART" -eq 1 ]; then
  docker compose start agent >/dev/null 2>&1 || docker compose up -d agent
fi

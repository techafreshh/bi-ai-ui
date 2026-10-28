#!/bin/sh
# Minimal boot entrypoint (issue #70): the agent boots DB-less. A missing
# data/app.db is the normal empty state: /health stays green and read_state
# degrades instead of raising. Manual database builds happen outside boot
# via the repo's reset script — nothing here generates data.
set -eu

exec "$@"

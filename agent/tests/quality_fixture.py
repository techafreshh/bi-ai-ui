"""Synthetic fixture DB builder for the data-quality check tests (issue #6).

Builds tiny SQLite databases with EXACTLY the row lists a test hands over,
so the checks' shares and coverage compute to hand-verifiable numbers
(e.g. 40 duplicate copies in 200 rows == share 0.2) instead of the seeded
DB's statistical shares. Used only to prove the checks discriminate — the
real seeded DB stays the runtime gate's source of truth.

Schema comes from scripts/generate_data.py's SCHEMA_SQL imported verbatim,
so fixture DBs can never drift from the real schema (zero re-declaration).
The builder is deliberately segment-agnostic: it fills the accounts table
only to satisfy usage_events' foreign key (db.connect enables FK
enforcement), and the checks never read segment — the Startup-only shape
was the generator's planting choice, not a check dimension. Only
usage_events rows are inserted; subscription/revenue tables stay empty,
which the freshness check reads as uncovered days when a test wants that.
"""

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
# scripts/ is not a package: put it on sys.path so `import generate_data`
# resolves for the verbatim SCHEMA_SQL import.
sys.path.insert(0, str(AGENT_DIR / "scripts"))

from generate_data import SCHEMA_SQL  # noqa: E402

import db  # noqa: E402  (import must follow the sys.path bootstrap above)

# (account_id, event_date, metric, units) insert columns, in schema order.
_USAGE_INSERT = (
    "INSERT INTO usage_events (account_id, event_date, metric, units) "
    "VALUES (?, ?, ?, ?)"
)


def build_fixture(path, usage_rows, segments=None):
    """Create a fresh fixture DB at `path` holding the generator's schema
    and exactly `usage_rows` in usage_events — one synthetic account per
    distinct account_id referenced (name/plan/arr/created_at are fixed
    placeholders the checks never read). Rows are (account_id, event_date,
    metric, units) tuples, mirroring the generator's insert column order.
    `segments` optionally maps account_id -> accounts.segment (issue #17's
    problem detection reads the segment); None keeps the historical
    all-Startup default. Returns the path."""
    if path.exists():
        # Unlink first so reruns over an existing path are idempotent
        # instead of appending onto the old rows and skewing every share.
        path.unlink()
    conn = db.connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        account_ids = sorted({row[0] for row in usage_rows})
        segments = segments or {}
        conn.executemany(
            "INSERT INTO accounts (id, name, segment, plan, arr_cents, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    account_id,
                    f"Fixture Co {account_id}",
                    segments.get(account_id, "Startup"),
                    "Starter",
                    180_000,
                    "2024-10-01",
                )
                for account_id in account_ids
            ),
        )
        conn.executemany(_USAGE_INSERT, usage_rows)
        conn.commit()
    finally:
        conn.close()
    return path

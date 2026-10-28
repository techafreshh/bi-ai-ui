"""Verbatim SQLite implementation of the `Source` protocol (issue #78).

`SqliteSource` wraps the current SQLite behavior unchanged so the move
cannot drift: `connect_ro` delegates to the existing
`db.connect(path, readonly=True)` settings (mode=ro URI, `sqlite3.Row`
factory, `PRAGMA foreign_keys = ON`, 10s timeout), and
`latest_event_day` runs the union-`MAX(event_date)` template moved
verbatim from `queries.py:_SQL_LATEST_EVENT_DATE` (the same union-MAX
shape `quality.py` uses for its default scope). No SQL is re-derived
here; a Postgres `Source` later reuses the contract, never this file.

Stdlib only, mirroring db.py: `os`/`pathlib` for the stored path, so
importing this module never touches the agent's model dependencies.
"""

import os
from pathlib import Path

try:  # package-style entry (import agent.data.sqlite_source from the repo root)
    from agent import db
except ModuleNotFoundError:  # script-style entry (import data.sqlite_source from agent/)
    import db

try:  # package-style entry (import agent.data.sqlite_source from the repo root)
    from agent.data.protocol import Source
except ModuleNotFoundError:  # script-style entry (import data.sqlite_source from agent/)
    from data.protocol import Source

# The only tables a source profile counts (the generator's four), sorted
# in the response; anything else in the file is not event/account data.
_KNOWN_TABLES = (
    "accounts",
    "revenue_events",
    "subscription_events",
    "usage_events",
)

# Latest event day across all three event tables: per-table MAX rides the
# event_date indexes, the outer MAX folds the three candidates (the same
# union-MAX shape quality.py uses for its default scope). Params: none.
# Verbatim move of queries.py:_SQL_LATEST_EVENT_DATE — byte-identical so
# the SQLite behavior cannot drift under the protocol.
_SQL_LATEST_EVENT_DATE = """
SELECT MAX(d) FROM (
    SELECT MAX(event_date) AS d FROM subscription_events
    UNION ALL
    SELECT MAX(event_date) FROM revenue_events
    UNION ALL
    SELECT MAX(event_date) FROM usage_events
)
"""


class SqliteSource(Source):
    """The `Source` protocol over one SQLite file.

    `path`: filesystem path of the database; when None (the default) the
    connection resolves the active dataset exactly like `db.connect_ro`
    does (issue #75 registry, then `DB_PATH`, then `DEFAULT_DB_PATH`), so
    existing callers need no edits. Stored as a `Path` (None stays None
    so the resolution stays lazy — a test swapping `db.DEFAULT_DB_PATH`
    still lands). Anything else (not a path, not None) is ValueError
    before any file is opened — the same fail-before-open discipline
    `queries.py:_require_date` keeps on date input.
    """

    def __init__(self, path=None):
        if path is None:
            self._path = None
        elif isinstance(path, (str, os.PathLike)):
            self._path = Path(path)
        else:
            raise ValueError(
                f"path must be a filesystem path or None, got {path!r}"
            )

    def connect_ro(self):
        """Open the database read-only via `db.connect(readonly=True)`.

        Delegates verbatim — `mode=ro` URI (writes raise instead of
        mutating, a missing file fails instead of being created), with the
        same row-factory/pragma/timeout settings as every other reader.
        The caller closes the connection (usually in `finally`).
        """
        return db.connect(self._path, readonly=True)

    def tables(self):
        """The known event/account tables actually present, sorted.

        Reads `sqlite_master` over its own read-only connection (closed in
        `finally`, mirroring the `datasets.py` read-only probe that never
        creates a missing file) and reports only the generator's four
        tables that are really there, sorted.
        """
        conn = self.connect_ro()
        try:
            present = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            conn.close()
        return sorted(name for name in _KNOWN_TABLES if name in present)

    def latest_event_day(self):
        """Latest observed event day as an ISO string, or None.

        Runs the verbatim union-`MAX(event_date)` template over its own
        read-only connection (closed in `finally`, the same
        try/finally shape `queries.latest_event_date` keeps): the data's
        own horizon, never a wall clock; None on a DB with no events.
        """
        conn = self.connect_ro()
        try:
            return conn.execute(_SQL_LATEST_EVENT_DATE).fetchone()[0]
        finally:
            conn.close()

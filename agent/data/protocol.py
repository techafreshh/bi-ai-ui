"""Data-source protocol (issue #78): the seam Postgres slots into.

Callers (`graph.py`, `quality.py`) depend on this `Source` contract —
never on `sqlite3` or on any one backend — so a future Postgres source
can replace the SQLite one without touching the read path. The contract
is deliberately narrow: a read-only connection plus the two metadata
probes the agent actually needs (which tables exist, what the latest
event day is). Every query template stays where it lives (`queries.py`,
`quality.py`); the protocol carries no SQL.

Stdlib only, mirroring db.py: `typing` for the contract (`sqlite3` only
for the connection type), so importing this module never touches the
agent's model dependencies.
"""

import sqlite3
from typing import Protocol, runtime_checkable


@runtime_checkable
class Source(Protocol):
    """Structural read-only data-source contract.

    Runtime-checkable so `isinstance(source, Source)` proves the seam a
    backend claims to fill; structural so backends implement it by
    matching the three members, never by importing this module.
    """

    def connect_ro(self) -> sqlite3.Connection:
        """Open a read-only connection to the source.

        Read-only means writes fail loudly instead of mutating — a
        missing file fails instead of being created — with the same
        row-factory/pragma/timeout settings every reader relies on. The
        caller owns the connection and closes it (usually in `finally`).
        """
        ...

    def tables(self) -> list[str]:
        """The known event/account tables actually present, sorted.

        Reports only the generator's four tables (`accounts`,
        `revenue_events`, `subscription_events`, `usage_events`) that the
        source really holds — anything else in the file is not event data
        — sorted so the listing is deterministic. Opens (and closes) its
        own read-only connection.
        """
        ...

    def latest_event_day(self) -> str | None:
        """Latest observed event day as an ISO 'YYYY-MM-DD' string.

        The union-`MAX(event_date)` across the event tables — the
        deterministic "data_end" anchor callers resolve windows against,
        never a wall clock — or None on a source with no events at all.
        Opens (and closes) its own read-only connection.
        """
        ...

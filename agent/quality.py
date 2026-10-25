"""Data-quality checks over the trailing DQ window (PRD data story).

The window is anchored to the store horizon (``DQ_WINDOW_DAYS`` trailing
days), so check results never drift as wall-clock time moves on. Semantics:

- ``freshness``   ok when the newest usage event is at most 2 days old.
- ``duplicates``  fail when exact-duplicate (account, ts, metric, amount)
                  groups exist in the window — the planted Sept ingest bug.
- ``nulls``       fail when the NULL-amount share of window events exceeds
                  the 2% breach threshold.
"""

from __future__ import annotations

import sqlite3

import queries
import store

NULL_BREACH = 0.02  # null share above which the nulls check fails
MAX_AGE_DAYS = 2  # freshness: horizon − newest event ≤ 2 days

# Probe-facing contract: the card surfaces exactly these three checks.
CHECK_NAMES = ("freshness", "duplicates", "nulls")


def dq_window(today: store.date | None = None) -> tuple[str, str]:
    """Trailing-window bounds (inclusive) as ISO dates, horizon-anchored."""
    today = today or store.HORIZON
    start = today - store.timedelta(days=store.DQ_WINDOW_DAYS - 1)
    return start.isoformat(), today.isoformat()


def dq_window_bounds_ts(start: str, end: str) -> tuple[str, str]:
    """ISO-datetime bounds covering every event on the boundary days."""
    return f"{start}T00:00:00", f"{end}T23:59:59"


def dq_checks(conn: sqlite3.Connection, today: store.date | None = None) -> dict:
    """Run the three checks and return card props + machine-readable summary.

    Returns ``{"checks": [...], "null_ratio": float, "dup_groups": int,
    "max_event_ts": str | None}``. Deterministic for a given database.
    """
    start, end = dq_window(today)
    ts_start, ts_end = dq_window_bounds_ts(start, end)
    counts = queries.run_query(conn, "dq_window_counts", {"start": ts_start, "end": ts_end})[0]
    dups = queries.run_query(conn, "dq_duplicate_groups", {"start": ts_start, "end": ts_end})[0]

    total = counts["total"] or 0
    nulls = counts["nulls"] or 0
    null_ratio = (nulls / total) if total else 0.0
    dup_groups = dups["groups"] or 0

    newest_day = counts["max_ts"][:10] if counts["max_ts"] else None
    if newest_day:
        age_days = ((today or store.HORIZON) - store.date.fromisoformat(newest_day)).days
    else:
        age_days = 10**6  # no events at all: maximally stale

    freshness_status = "ok" if age_days <= MAX_AGE_DAYS else "fail"
    duplicates_status = "fail" if dup_groups > 0 else "ok"
    nulls_status = "fail" if null_ratio > NULL_BREACH else "ok"

    return {
        "checks": [
            # Order mirrors PRD §7: freshness, duplicates, nulls.
            {"name": "freshness", "status": freshness_status, "score": 1.0 if age_days <= MAX_AGE_DAYS else 0.0},
            {"name": "duplicates", "status": duplicates_status, "score": round((dups["extra_rows"] or 0) / total, 4) if total else 0.0},
            {"name": "nulls", "status": nulls_status, "score": round(null_ratio, 4)},
        ],
        "null_ratio": null_ratio,
        "dup_groups": dup_groups,
        "max_event_ts": counts["max_ts"],
        "window": f"{start}..{end}",
        "bug_window": f"{store.BUG_START.isoformat()}..{store.BUG_END.isoformat()}",
    }

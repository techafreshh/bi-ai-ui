"""Read-only Postgres second source with SQLite template parity (issue #80).

`PostgresSource` answers the same queries as the SQLite path (`queries.py`
/ `quality.py`) with zero code change at the call sites: `queries.py`,
`quality.py` and `datasets.py` delegate here whenever `DATASOURCE_URL` is
set, and `scripts/load_postgres.sh` loads the same `samples/*.csv` rows
the SQLite sample path serves, so both backends return identical tiles,
tables and quality evidence.

Security model mirrors `queries.py:56-68`: every SQL statement is a
static module-level `_PG_SQL_*` template; values travel ONLY as bound
`%s` / `%(name)s` parameters (psycopg3's `pyformat` paramstyle — never
f-strings, never `%` interpolation), and caller-supplied dates are
canonicalized via `date.fromisoformat` (ValueError on anything else,
injection payloads included) before any connection opens. Connections
come only from `_connect()` — `psycopg.connect` with libpq
`options="-c default_transaction_read_only=on -c statement_timeout=5000"`
plus `autocommit=True`, so the session itself refuses writes even if a
template ever slipped one in — and `_require_ro()` refuses to connect at
all unless `DATASOURCE_RO` holds a read-only spelling (unset defaults to
read-only, mirroring `db.is_readonly`). This module never writes, mutates
DDL, or even opens a write handle; the loader script owns all DDL.

Dialect notes (the only intentional divergences from the SQLite
templates): `?` becomes `%s` and `:name` becomes `%(name)s`; the recursive
calendar CTE becomes `generate_series(%s::date, %s::date, '1 day')`; the
`julianday(a) - julianday(b)` day difference becomes the integer
`(a::date - b::date)` (both sides are `TEXT` ISO days, verbatim from the
sandbox schema, so the cast is exact); `LIMIT :limit` becomes
`LIMIT %(limit)s`. Bound-param order is documented per template.

psycopg imports ride inside `_connect()` (with a top-level `None`
fallback) so importing this module never requires the driver — the SQLite
path keeps working on a driverless interpreter; only connecting needs it.
"""

import os
from datetime import date, timedelta

try:  # package-style entry (import agent.data.postgres_source from repo root)
    from agent.data.protocol import Source
except ModuleNotFoundError:  # script-style entry (import postgres_source)
    from data.protocol import Source

try:
    import psycopg as _psycopg
except ImportError:  # driverless interpreter: SQLite path still imports
    _psycopg = None

# The only tables a source profile counts (the generator's four), sorted
# in the response; anything else in the database is not event data.
_KNOWN_TABLES = (
    "accounts",
    "revenue_events",
    "subscription_events",
    "usage_events",
)

# Tables carrying `event_date`; `latest_event_day` is their union-MAX.
_EVENT_TABLES = (
    "subscription_events",
    "revenue_events",
    "usage_events",
)

# Fallback defaults for the datasource vars (issue #79), mirrored from
# agent/db.py and agent/config.py's registry rows: DATASOURCE_URL names
# the Postgres database (empty = local SQLite path resolution as today);
# DATASOURCE_RO guards every connection (only 1/true/yes connect).
_DATASOURCE_URL_DEFAULT = ""
_DATASOURCE_RO_DEFAULT = "1"

# Spellings that count as read-only for DATASOURCE_RO (case-insensitive,
# whitespace-stripped); anything else — including "0" or empty — means
# "not read-only" and _connect refuses with a read-only RuntimeError.
_READONLY_VALUES = ("1", "true", "yes")

# libpq startup options for every connection: the session is read-only by
# construction (writes fail server-side) and bounded to 5s statements.
_CONNECT_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=5000"
)

# Average month length in days (365.25 / 12) for tenure in months.
_DAYS_PER_MONTH = 30.4375

# Fallback default for SQL_ROW_LIMIT (issue #79), mirrored from
# agent/config.py's registry row: the row cap applied when a caller leaves
# `limit` unset.
_SQL_ROW_LIMIT_FALLBACK = "50"

# The only metric churn_delta accepts.
_CHURN_METRICS = ("churn_rate",)

# Net-new ARR in a window: every event type participates (cancel markers
# carry arr_delta_cents = 0). Params: (start, end).
_PG_SQL_NET_NEW_ARR = """
SELECT COALESCE(SUM(arr_delta_cents), 0)
FROM subscription_events
WHERE event_date BETWEEN %s AND %s
"""

# Point-in-time state on a day: per account, the ARR of its events <= day
# (cancel rows carry a 0 delta, so the sum is unaffected) plus a
# churned-by-day flag; the outer WHERE keeps only still-active accounts.
# Params: (day,).
_PG_SQL_ACTIVE_DAY = """
SELECT COALESCE(SUM(cents), 0) AS active_arr_cents,
       COUNT(*) AS active_accounts
FROM (
    SELECT SUM(arr_delta_cents) AS cents,
           MAX(CASE WHEN event_type = 'cancel' THEN 1 ELSE 0 END) AS churned
    FROM subscription_events
    WHERE event_date <= %s
    GROUP BY account_id
) AS active
WHERE churned = 0
"""

# NRR cohort replay: the cohort — accounts with a 'new' event <= start —
# priced at start (members already churned by start read 0) versus at end
# (members churned by end read 0). Params: {"start", "end"}.
_PG_SQL_NRR = """
WITH cohort AS (
    SELECT DISTINCT account_id
    FROM subscription_events
    WHERE event_type = 'new' AND event_date <= %(start)s
),
per_account AS (
    SELECT s.account_id,
           MAX(CASE WHEN s.event_date <= %(start)s AND s.event_type = 'cancel'
                    THEN 1 ELSE 0 END) AS churned_at_start,
           MAX(CASE WHEN s.event_date <= %(end)s AND s.event_type = 'cancel'
                    THEN 1 ELSE 0 END) AS churned_at_end,
           COALESCE(SUM(CASE WHEN s.event_date <= %(start)s
                             THEN s.arr_delta_cents ELSE 0 END), 0) AS arr_at_start,
           COALESCE(SUM(CASE WHEN s.event_date <= %(end)s
                             THEN s.arr_delta_cents ELSE 0 END), 0) AS arr_at_end
    FROM subscription_events s
    JOIN cohort c ON c.account_id = s.account_id
    GROUP BY s.account_id
)
SELECT COALESCE(SUM(CASE WHEN churned_at_start = 1 THEN 0
                         ELSE arr_at_start END), 0) AS cohort_arr_at_start,
       COALESCE(SUM(CASE WHEN churned_at_end = 1 THEN 0
                         ELSE arr_at_end END), 0) AS cohort_arr_at_end
FROM per_account
"""

# Daily ARR replay in one pass: calendar is generate_series over the
# window; `windowed` computes, per account, the running ARR after each
# event and the day the NEXT event changes it (LEAD over ALL event types);
# `moves` keeps only non-cancel rows, so each becomes the interval
# [effective_day, next_event_day) and a cancel ends its account BEFORE its
# own day (no interval is built from it, and the prior interval stops at
# its LEAD). Rows starting at or after the account's churn are also
# dropped — the seed contains post-cancel upgrade markers, which would
# otherwise resurrect the account past its cancel. Filtering dates before
# the window computation is what keeps post-window upgrades out of running
# sums. LEFT JOIN keeps calendar days no interval covers at 0.0.
# NOTE on param order: %s #1 = start (generate_series anchor), #2 = end
# (generate_series bound), #3 = end again (event filter) — hence the
# (start, end, end) tuple.
_PG_SQL_ARR_SERIES = """
WITH calendar(day) AS (
    SELECT generate_series(%s::date, %s::date, '1 day')::text
),
windowed AS (
    SELECT account_id, event_date, event_type,
           SUM(arr_delta_cents) OVER (
               PARTITION BY account_id
               ORDER BY event_date, id
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS arr_cents,
           LEAD(event_date) OVER (
               PARTITION BY account_id
               ORDER BY event_date, id
           ) AS next_event_day,
           MAX(CASE WHEN event_type = 'cancel' THEN event_date END) OVER (
               PARTITION BY account_id
           ) AS cancelled_on
    FROM subscription_events
    WHERE event_date <= %s
),
moves AS (
    SELECT event_date AS effective_day, arr_cents, next_event_day
    FROM windowed
    WHERE event_type <> 'cancel'
      AND (cancelled_on IS NULL OR event_date < cancelled_on)
)
SELECT c.day AS date,
       ROUND(COALESCE(SUM(m.arr_cents), 0) / 100.0, 2) AS arr
FROM calendar c
LEFT JOIN moves m
  ON m.effective_day <= c.day
 AND (m.next_event_day IS NULL OR c.day < m.next_event_day)
GROUP BY c.day
ORDER BY c.day
"""

# One row per churn event in the window; mrr divides churn-time ARR
# (accounts.arr_cents) by 12 and tenure divides the exact day count by
# 30.4375 (a ::date - ::date difference counts whole days, matching the
# julianday-of-midnight-ISO semantics). Params: (window_start, window_end,
# limit).
_PG_SQL_CHURN_COHORT = """
SELECT a.segment AS segment,
       ROUND(a.arr_cents / 1200.0, 2) AS mrr,
       ROUND((s.event_date::date - a.created_at::date) / 30.4375, 1)
           AS tenure_months,
       a.plan AS plan
FROM subscription_events s
JOIN accounts a ON a.id = s.account_id
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN %s AND %s
ORDER BY s.event_date, s.account_id
LIMIT %s
"""

# Churn-spike counts over a window: every cancel in [start, end] plus —
# per row — whether the SAME account has an 'upgrade' (the repricing event
# of the planted pricing change) in [paired_from, cancel_date].
# Params: {"start", "end", "paired_from"}.
_PG_SQL_CHURN_REPRICED = """
SELECT COUNT(*) AS cancelled,
       SUM(CASE WHEN EXISTS (
               SELECT 1 FROM subscription_events u
               WHERE u.account_id = s.account_id
                 AND u.event_type = 'upgrade'
                 AND u.event_date >= %(paired_from)s
                 AND u.event_date <= s.event_date
           ) THEN 1 ELSE 0 END) AS repriced
FROM subscription_events s
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN %(start)s AND %(end)s
"""

# The drill-down's row-level cohort: _PG_SQL_CHURN_COHORT plus the cancel
# date itself, the churn-time plan and the repricing flag (same signature
# as _PG_SQL_CHURN_REPRICED, evaluated against the cohort window's start).
# `segment` is bound as None for "all segments" — a NULL parameter
# short-circuits the OR, so one static template serves both shapes.
# Params: {"start", "end", "paired_from", "segment", "limit"}.
_PG_SQL_CHURN_COHORT_DETAIL = """
SELECT a.segment AS segment,
       a.plan AS plan,
       ROUND(a.arr_cents / 1200.0, 2) AS mrr,
       ROUND((s.event_date::date - a.created_at::date) / 30.4375, 1)
           AS tenure_months,
       s.event_date AS cancelled_on,
       CASE WHEN EXISTS (
               SELECT 1 FROM subscription_events u
               WHERE u.account_id = s.account_id
                 AND u.event_type = 'upgrade'
                 AND u.event_date >= %(paired_from)s
                 AND u.event_date <= s.event_date
           ) THEN 'yes' ELSE 'no' END AS repriced
FROM subscription_events s
JOIN accounts a ON a.id = s.account_id
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN %(start)s AND %(end)s
  AND (CAST(%(segment)s AS TEXT) IS NULL OR a.segment = %(segment)s)
ORDER BY s.event_date, s.account_id
LIMIT %(limit)s
"""

# Churned ARR in a window: the churn-time ARR of every cancelling account
# (one cancel event per account by construction).
# Params: (start, end).
_PG_SQL_CHURNED_ARR = """
SELECT COALESCE(SUM(a.arr_cents), 0)
FROM subscription_events s
JOIN accounts a ON a.id = s.account_id
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN %s AND %s
"""

# Latest event day across all three event tables: per-table MAX folds the
# three candidates. Params: none.
_PG_SQL_LATEST_EVENT_DATE = """
SELECT MAX(d) FROM (
    SELECT MAX(event_date) AS d FROM subscription_events
    UNION ALL
    SELECT MAX(event_date) FROM revenue_events
    UNION ALL
    SELECT MAX(event_date) FROM usage_events
) AS latest
"""

# Distinct event days covered inside the scope, over the union of the
# three event tables. Params: (start, end) x3.
_PG_SQL_COVERED_DAYS = """
SELECT COUNT(DISTINCT d) FROM (
    SELECT event_date AS d FROM subscription_events WHERE event_date BETWEEN %s AND %s
    UNION
    SELECT event_date FROM revenue_events WHERE event_date BETWEEN %s AND %s
    UNION
    SELECT event_date FROM usage_events WHERE event_date BETWEEN %s AND %s
) AS covered
"""

# (redundant rows, total rows) for usage in scope: redundant = SUM(n - 1)
# over exact-tuple groups with more than one row. Params: (start, end) x2.
_PG_SQL_DUPLICATE_SHARE = """
SELECT
    (SELECT COALESCE(SUM(n - 1), 0) FROM (
        SELECT COUNT(*) AS n FROM usage_events
        WHERE event_date BETWEEN %s AND %s
        GROUP BY account_id, event_date, metric, units
        HAVING COUNT(*) > 1
    ) AS dup),
    (SELECT COUNT(*) FROM usage_events WHERE event_date BETWEEN %s AND %s)
"""

# (null-units rows, total rows) for usage in scope. Params: (start, end) x2.
_PG_SQL_NULL_SHARE = """
SELECT
    (SELECT COUNT(*) FROM usage_events
     WHERE event_date BETWEEN %s AND %s AND units IS NULL),
    (SELECT COUNT(*) FROM usage_events WHERE event_date BETWEEN %s AND %s)
"""

# Dirty usage rows per (event_date, segment): exact-duplicate EXTRAS only —
# SUM(n - 1) over the same (account_id, event_date, metric, units) groups
# _PG_SQL_DUPLICATE_SHARE counts — joined to accounts for the segment.
# Params: (start, end).
_PG_SQL_PROBLEM_DUP_ROWS = """
SELECT event_date AS d, segment, SUM(n - 1) AS extras
FROM (
    SELECT ue.account_id, ue.event_date, ue.metric, ue.units,
           a.segment AS segment, COUNT(*) AS n
    FROM usage_events ue
    JOIN accounts a ON a.id = ue.account_id
    WHERE ue.event_date BETWEEN %s AND %s
    GROUP BY ue.account_id, ue.event_date, ue.metric, ue.units, a.segment
    HAVING COUNT(*) > 1
) AS dup
GROUP BY event_date, segment
"""

# Dirty usage rows per (event_date, segment): NULL-units rows, mirroring
# _PG_SQL_NULL_SHARE's numerator. Params: (start, end).
_PG_SQL_PROBLEM_NULL_ROWS = """
SELECT ue.event_date AS d, a.segment AS segment, COUNT(*) AS n
FROM usage_events ue
JOIN accounts a ON a.id = ue.account_id
WHERE ue.event_date BETWEEN %s AND %s
  AND ue.units IS NULL
GROUP BY ue.event_date, a.segment
"""

# Known event/account tables actually present, sorted. Params: (names,).
_PG_SQL_TABLES = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = ANY(%s)
"""

# Default scope for the no-arg quality call: the N trailing days ending at
# the latest observed event date (mirrors quality.DEFAULT_SCOPE_DAYS).
_DEFAULT_SCOPE_DAYS = 30

# Freshness band: 1-3 uncovered calendar days warn; more than that, or a
# scope with no events at all, fails.
_FRESHNESS_MAX_WARN_GAP_DAYS = 3

# Duplicates band: sub-0.2% redundant rows warn; above 1% fails.
_DUPLICATE_FAIL_SHARE = 0.01
_DUPLICATE_WARN_SHARE = 0.002

# Nulls band: the PRD's literal breach line is null units > 2% (strict);
# up to 0.1% is transient-pipeline noise.
_NULL_FAIL_SHARE = 0.02
_NULL_WARN_SHARE = 0.001

# Fixed result order — consumers index the list positionally.
_CHECK_NAMES = ("freshness", "duplicates", "nulls")

# Issue #17 problem detection: how far back the corruption scan reaches.
_PROBLEM_LOOKBACK_DAYS = 120


def use_postgres():
    """True when DATASOURCE_URL names a Postgres database (issue #80).

    A non-blank value selects this module at every delegation seam
    (`queries.py`, `quality.py`, `datasets.py`); empty or unset means
    local SQLite path resolution as today. Validated for non-blankness
    only — the URL itself is the operator's connection string."""
    return bool(os.getenv("DATASOURCE_URL", _DATASOURCE_URL_DEFAULT).strip())


def _require_ro():
    """Guard every connection on the DATASOURCE_RO read-only spelling.

    Unset defaults to read-only (mirroring `db.is_readonly`); anything but
    1/true/yes raises RuntimeError naming the read-only violation before
    any connection opens — the fail-before-open idiom `queries.py` keeps
    on date input."""
    raw = os.getenv("DATASOURCE_RO", _DATASOURCE_RO_DEFAULT)
    if (raw or "").strip().lower() not in _READONLY_VALUES:
        raise RuntimeError(
            f"refusing Postgres query: DATASOURCE_RO={raw!r} is not "
            "read-only — the agent serves read-only queries and must not "
            "connect with a writable datasource"
        )
    return raw


def _connect():
    """Open a least-privilege read-only Postgres session (issue #80).

    `psycopg.connect` with libpq `options` pinning
    `default_transaction_read_only=on` (writes fail server-side even if a
    template ever slipped one in) and a 5s `statement_timeout`, plus
    `autocommit=True` so read probes never hold a transaction open.
    Raises RuntimeError naming `psycopg` when the driver is missing, or
    naming the read-only violation when DATASOURCE_RO is not a read-only
    spelling. The caller closes the connection (usually in `finally`).
    """
    _require_ro()
    if _psycopg is None:  # pragma: no cover - resolved by the dependency
        raise RuntimeError(
            "psycopg is not installed: pip install 'psycopg[binary]' "
            "to query the Postgres source"
        )
    url = os.getenv("DATASOURCE_URL", _DATASOURCE_URL_DEFAULT)
    return _psycopg.connect(url, autocommit=True, options=_CONNECT_OPTIONS)


def _default_limit():
    """Row cap from SQL_ROW_LIMIT, clamped to >= 1 (mirrors queries)."""
    raw = os.getenv("SQL_ROW_LIMIT", _SQL_ROW_LIMIT_FALLBACK)
    if not raw or not raw.strip():
        return int(_SQL_ROW_LIMIT_FALLBACK)
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return int(_SQL_ROW_LIMIT_FALLBACK)
    if value < 1:
        return int(_SQL_ROW_LIMIT_FALLBACK)
    return value


def _require_date(value, label):
    """Validate `value` as a calendar date, returning the canonical
    'YYYY-MM-DD' form. ValueError on anything else — including SQL
    injection payloads — so only this canonical string is ever bound into
    SQL. Identical contract to queries._require_date."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO 'YYYY-MM-DD' date string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{label} is not a valid ISO date: {value!r}") from None


def _require_window(start, end):
    """Both bounds valid and ordered; returns the canonical pair."""
    start = _require_date(start, "window start")
    end = _require_date(end, "window end")
    if start > end:
        raise ValueError(f"window start {start} is after window end {end}")
    return start, end


def _require_window_tuple(window):
    """A churn window must arrive as an exact (start, end) pair; strings and
    other lengths are rejected before any date parsing."""
    if isinstance(window, (str, bytes)) or not isinstance(window, (tuple, list)):
        raise ValueError("window must be a (start, end) pair of ISO date strings")
    if len(window) != 2:
        raise ValueError("window must be a (start, end) pair of ISO date strings")
    return _require_window(window[0], window[1])


def _require_int_at_least(value, label, minimum):
    """Reject bools (an int subclass) and anything below `minimum`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _active_state(conn, day):
    """(active ARR cents, active account count) on `day` — the shared
    point-in-time rule behind mrr/accounts and every churn-rate denominator."""
    row = conn.execute(_PG_SQL_ACTIVE_DAY, (day,)).fetchone()
    return row[0], row[1]


def _churn_rate(conn, window_start, window_end):
    """Churn rate of [window_start, window_end]: churned ARR in the window
    divided by active ARR at the window's first day (0.0 when there was
    nothing active)."""
    churned_cents = conn.execute(
        _PG_SQL_CHURNED_ARR, (window_start, window_end)
    ).fetchone()[0]
    active_cents = _active_state(conn, window_start)[0]
    if active_cents <= 0:
        return 0.0
    return churned_cents / active_cents


def kpis(start, end):
    """Monthly KPI block over the inclusive window: net_new_arr (dollars),
    mrr (active ARR at `end` / 12), nrr (cohort ARR at `end` / at `start`,
    0.0 on a zero base) and accounts (active count at `end`). Identical
    return shape to queries.kpis."""
    start, end = _require_window(start, end)
    conn = _connect()
    try:
        net_new_cents = conn.execute(_PG_SQL_NET_NEW_ARR, (start, end)).fetchone()[0]
        active_arr_cents, active_accounts = _active_state(conn, end)
        cohort_arr_at_start, cohort_arr_at_end = conn.execute(
            _PG_SQL_NRR, {"start": start, "end": end}
        ).fetchone()
    finally:
        conn.close()
    return {
        "net_new_arr": round(net_new_cents / 100, 2),
        "mrr": round(active_arr_cents / 1200, 2),
        "nrr": cohort_arr_at_end / cohort_arr_at_start if cohort_arr_at_start else 0.0,
        "accounts": active_accounts,
    }


def gross_churned_arr(start, end):
    """Gross churned ARR in dollars over the inclusive window (mirrors
    queries.gross_churned_arr)."""
    start, end = _require_window(start, end)
    conn = _connect()
    try:
        churned_cents = conn.execute(
            _PG_SQL_CHURNED_ARR, (start, end)
        ).fetchone()[0]
    finally:
        conn.close()
    return round(churned_cents / 100, 2)


def active_accounts_eom(end):
    """Active-account count at the day `end`. Mirrors
    queries.active_accounts_eom (single-arg: the window end anchors the
    read)."""
    day = _require_date(end, "end")
    conn = _connect()
    try:
        row = conn.execute(_PG_SQL_ACTIVE_DAY, (day,)).fetchone()
    finally:
        conn.close()
    return row[1]


def arr_series(start, end):
    """One {"date", "arr"} row per calendar day from `start` to `end`
    inclusive, `arr` = total active ARR that day in dollars. Identical
    return shape to queries.arr_series."""
    start, end = _require_window(start, end)
    conn = _connect()
    try:
        rows = conn.execute(_PG_SQL_ARR_SERIES, (start, end, end)).fetchall()
    finally:
        conn.close()
    return [{"date": row[0], "arr": float(row[1])} for row in rows]


def latest_event_date():
    """Latest observed event day across all three event tables as an ISO
    string, or None on a database with no events at all. Mirrors
    queries.latest_event_date."""
    conn = _connect()
    try:
        return conn.execute(_PG_SQL_LATEST_EVENT_DATE).fetchone()[0]
    finally:
        conn.close()


def churn_cohort(window, limit=None):
    """Churn cohort rows for the inclusive `window` (a (start, end) pair):
    one {"segment", "mrr", "tenure_months", "plan"} row per cancel event,
    ordered by (event_date, account_id), at most `limit` rows. Identical
    return shape to queries.churn_cohort."""
    start, end = _require_window_tuple(window)
    if limit is None:
        limit = _default_limit()
    _require_int_at_least(limit, "limit", 1)
    conn = _connect()
    try:
        rows = conn.execute(_PG_SQL_CHURN_COHORT, (start, end, limit)).fetchall()
    finally:
        conn.close()
    return [
        {
            "segment": row[0],
            "mrr": float(row[1]),
            "tenure_months": float(row[2]),
            "plan": row[3],
        }
        for row in rows
    ]


def _repriced_counts(conn, start, end, paired_from):
    """(cancelled, repriced) over the inclusive window: every 'cancel' in
    [start, end], of those the rows whose account also has an 'upgrade' in
    [paired_from, cancel_date]."""
    cancelled, repriced = conn.execute(
        _PG_SQL_CHURN_REPRICED,
        {"start": start, "end": end, "paired_from": paired_from},
    ).fetchone()
    # SUM over zero rows reads NULL, not 0 — the cancelled==0 case. SUM of
    # integers arrives as Decimal from psycopg: coerce to int so the
    # returned counts match the SQLite ints.
    return int(cancelled), int(repriced or 0)


def _dominant_segment(rows):
    """(segment, share) with the highest row count among the cohort `rows`
    the table actually shows (ties broken by count, then lexicographically,
    so the pick is deterministic); share is relative to len(rows), rounded
    to 4 decimals. No rows: (None, 0.0)."""
    if not rows:
        return None, 0.0
    counts = {}
    for row in rows:
        segment = row["segment"]
        counts[segment] = counts.get(segment, 0) + 1
    segment, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return segment, round(count / len(rows), 4)


def churn_driver(window, data_end=None, limit=None):
    """Churn-spike facts for the variance callout and the Investigate
    drill-down over `window` (a (start, end) pair of ISO dates). Identical
    semantics and return shape to queries.churn_driver."""
    start, end = _require_window_tuple(window)
    if data_end is not None:
        data_end = _require_date(data_end, "data_end")
    if limit is None:
        limit = _default_limit()
    _require_int_at_least(limit, "limit", 1)

    conn = _connect()
    try:
        # Spike detection runs strictly inside the user's window: repriced
        # cancels here are evidence the named window sits on the planted
        # spike, never a promise about observations outside it.
        _, window_repriced = _repriced_counts(conn, start, end, start)
        spiked = window_repriced > 0
        cohort_end = data_end if (spiked and data_end and data_end > end) else end
        cancelled, repriced = _repriced_counts(conn, start, cohort_end, start)
    finally:
        conn.close()
    rows = churn_cohort((start, cohort_end), limit=limit)
    dominant_segment, dominant_share = _dominant_segment(rows)
    return {
        "cohort_start": start,
        "cohort_end": cohort_end,
        "spiked": spiked,
        "cancelled": cancelled,
        "repriced": repriced,
        "rows": rows,
        "dominant_segment": dominant_segment,
        "dominant_share": dominant_share,
    }


def churn_cohort_detail(window, segment=None, limit=None):
    """Row-level churn cohort for the Investigate drill-down: one
    {"segment", "plan", "mrr", "tenure_months", "cancelled_on",
    "repriced"} dict per cancel event in the inclusive `window` (a (start,
    end) pair of ISO dates), ordered by (event_date, account_id), at most
    `limit` rows. Identical return shape to queries.churn_cohort_detail."""
    start, end = _require_window_tuple(window)
    if segment is not None:
        if not isinstance(segment, str) or not segment:
            raise ValueError("segment must be None or a non-empty string")
    if limit is None:
        limit = _default_limit()
    _require_int_at_least(limit, "limit", 1)
    conn = _connect()
    try:
        rows = conn.execute(
            _PG_SQL_CHURN_COHORT_DETAIL,
            {
                "start": start,
                "end": end,
                "paired_from": start,
                "segment": segment,
                "limit": limit,
            },
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "segment": row[0],
            "plan": row[1],
            "mrr": float(row[2]),
            "tenure_months": float(row[3]),
            "cancelled_on": row[4],
            "repriced": row[5],
        }
        for row in rows
    ]


def churn_delta(end, metric="churn_rate", baseline_months=6):
    """Churn variance at `end` against a trailing baseline: {"delta",
    "baseline", "current"} with the RELATIVE delta convention. Identical
    semantics and return shape to queries.churn_delta."""
    import calendar

    if metric not in _CHURN_METRICS:
        raise ValueError(
            f"metric {metric!r} is not whitelisted; "
            f"must be one of {_CHURN_METRICS}"
        )
    end_date = date.fromisoformat(_require_date(end, "end"))
    _require_int_at_least(baseline_months, "baseline_months", 1)

    current_month_start = end_date.replace(day=1)
    # Calendar month index arithmetic (year*12 + month-1) handles the
    # year wrap without any month module guesswork.
    current_index = end_date.year * 12 + end_date.month - 1
    conn = _connect()
    try:
        current = _churn_rate(
            conn, current_month_start.isoformat(), end_date.isoformat()
        )
        baseline_rates = []
        for back in range(1, baseline_months + 1):
            year, month_index = divmod(current_index - back, 12)
            month_start = date(year, month_index + 1, 1)
            month_end = date(
                year, month_index + 1, calendar.monthrange(year, month_index + 1)[1]
            )
            baseline_rates.append(
                _churn_rate(conn, month_start.isoformat(), month_end.isoformat())
            )
    finally:
        conn.close()
    baseline = sum(baseline_rates) / baseline_months
    if baseline != 0.0:
        delta = (current - baseline) / baseline
    else:
        delta = 1.0 if current > 0.0 else 0.0
    return {"delta": delta, "baseline": baseline, "current": current}


def _require_scope_pair(window):
    """A scope pair must arrive as an exact (start, end) pair; strings and
    other lengths are rejected before any date parsing. Same semantics as
    quality._require_scope_pair."""
    if isinstance(window, (str, bytes)) or not isinstance(window, (tuple, list)):
        raise ValueError("window must be a (start, end) pair of ISO date strings")
    if len(window) != 2:
        raise ValueError("window must be a (start, end) pair of ISO date strings")
    return _require_window(window[0], window[1])


def _share_status(share, fail_above, warn_above):
    """ok / warn / fail banding for a corrupt-rows share (strict fail line,
    mirroring the PRD's null% > 2% breach reading)."""
    if share > fail_above:
        return "fail"
    if share > warn_above:
        return "warn"
    return "ok"


def _latest_event_date_conn(conn):
    """Latest event day across all three event tables, or None when the
    database holds no events."""
    return conn.execute(_PG_SQL_LATEST_EVENT_DATE).fetchone()[0]


def _freshness(conn, start, end):
    """(score, status): covered calendar days / window days, banded by the
    count of missing days — 0 is ok, 1..3 warns, more than that (or a fully
    empty scope) fails."""
    covered_days = conn.execute(
        _PG_SQL_COVERED_DAYS, (start, end, start, end, start, end)
    ).fetchone()[0]
    window_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    missing_days = window_days - covered_days
    score = covered_days / window_days
    if covered_days == 0 or missing_days > _FRESHNESS_MAX_WARN_GAP_DAYS:
        status = "fail"
    elif missing_days > 0:
        status = "warn"
    else:
        status = "ok"
    return score, status


def _duplicates(conn, start, end):
    """(score, status): 1 - redundant-rows share of usage in scope. A
    zero-row scope is vacuously clean."""
    redundant_rows, total_rows = conn.execute(
        _PG_SQL_DUPLICATE_SHARE, (start, end, start, end)
    ).fetchone()
    # SUM arrives as Decimal from psycopg: coerce before the share math
    # so the score stays a float like the SQLite path.
    share = float(redundant_rows) / total_rows if total_rows else 0.0
    return 1.0 - share, _share_status(share, _DUPLICATE_FAIL_SHARE, _DUPLICATE_WARN_SHARE)


def _nulls(conn, start, end):
    """(score, status): 1 - NULL-units share of usage in scope."""
    null_rows, total_rows = conn.execute(
        _PG_SQL_NULL_SHARE, (start, end, start, end)
    ).fetchone()
    # COUNT arrives as int, but float() keeps the share a float even if a
    # driver ever returns Decimal here.
    share = float(null_rows) / total_rows if total_rows else 0.0
    return 1.0 - share, _share_status(share, _NULL_FAIL_SHARE, _NULL_WARN_SHARE)


def _result(name, score, status):
    """Clamp the raw metric into [0, 1], round to 4 decimals and coerce to
    float — the fixed row shape [{name, status, score}] consumers get."""
    clamped = min(1.0, max(0.0, float(score)))
    return {"name": name, "status": status, "score": float(round(clamped, 4))}


def run_checks(start=None, end=None, *, window=None):
    """Run the three data-quality checks and return them in the fixed order
    freshness, duplicates, nulls — [{name, status, score}]. Identical
    semantics, scope resolution and return shape to quality.run_checks."""
    if window is not None:
        if start is not None or end is not None:
            raise ValueError(
                "pass either window=(start, end) or start/end arguments, not both"
            )
        start, end = _require_scope_pair(window)
    if start is not None:
        start = _require_date(start, "start")
    if end is not None:
        end = _require_date(end, "end")

    conn = _connect()
    try:
        if end is None:
            # Anchor the default scope on the data's own latest day — never
            # a wall clock; an eventless database falls back to today, where
            # every check degenerates to its empty-scope reading.
            end = _latest_event_date_conn(conn) or date.today().isoformat()
        if start is None:
            start = (
                date.fromisoformat(end) - timedelta(days=_DEFAULT_SCOPE_DAYS - 1)
            ).isoformat()
        _require_window(start, end)
        metrics = {
            "freshness": _freshness(conn, start, end),
            "duplicates": _duplicates(conn, start, end),
            "nulls": _nulls(conn, start, end),
        }
    finally:
        conn.close()
    # Assembling from _CHECK_NAMES pins "exactly these three, in this order"
    # by construction.
    return [_result(name, *metrics[name]) for name in _CHECK_NAMES]


def _latest_dirty_run(iso_days):
    """The contiguous calendar-day run ending at the latest dirty day, as a
    (start_iso, end_iso) pair — or None when no days are given. Pure: feeds
    from the detection scan's own day strings."""
    days = sorted(_require_date(day, "dirty day") for day in iso_days)
    if not days:
        return None
    end = date.fromisoformat(days[-1])
    start = end
    remaining = set(days)
    while (start - timedelta(days=1)).isoformat() in remaining:
        start -= timedelta(days=1)
    return start.isoformat(), end.isoformat()


def recent_problem_scope(lookback_days=_PROBLEM_LOOKBACK_DAYS):
    """Detect the recent window where the data is actually dirty. Identical
    semantics and return shape to quality.recent_problem_scope."""
    if (
        isinstance(lookback_days, bool)
        or not isinstance(lookback_days, int)
        or lookback_days < 1
    ):
        raise ValueError(
            f"lookback_days must be a positive integer, got {lookback_days!r}"
        )
    conn = _connect()
    try:
        end = _latest_event_date_conn(conn)
        if not end:
            # Eventless database: no anchor day, nothing to scope.
            return None
        # The TEXT ISO days compare chronologically as strings, so the
        # timedelta arithmetic below anchors on parsed dates only.
        start = (
            date.fromisoformat(end) - timedelta(days=lookback_days - 1)
        ).isoformat()
        # (day, segment) -> dirty-row count: duplicate extras + NULL-units
        # rows, i.e. exactly the rows the two share checks would penalize.
        dirty = {}
        for day, segment, extras in conn.execute(
            _PG_SQL_PROBLEM_DUP_ROWS, (start, end)
        ):
            dirty[(day, segment)] = dirty.get((day, segment), 0) + (extras or 0)
        for day, segment, rows in conn.execute(
            _PG_SQL_PROBLEM_NULL_ROWS, (start, end)
        ):
            dirty[(day, segment)] = dirty.get((day, segment), 0) + (rows or 0)
        if not dirty:
            return None
        run_start, run_end = _latest_dirty_run([day for day, _ in dirty])
        segment_counts = {}
        for (day, segment), rows in dirty.items():
            # The segment must describe the returned run, not the whole
            # lookback: dirt outside the run is a different (older) story.
            if run_start <= day <= run_end:
                segment_counts[segment] = segment_counts.get(segment, 0) + rows
        segment = min(
            segment_counts, key=lambda name: (-segment_counts[name], name)
        )
        return {"start": run_start, "end": run_end, "segment": segment}
    finally:
        conn.close()


# Compiler dispatch (issue #77): every registry name maps onto an EXISTING
# implementation above — no new _PG_SQL_* strings live behind this map.
_PG_METRIC_FUNCS = {
    "net_new_arr": lambda s, e: kpis(s, e)["net_new_arr"],
    "mrr": lambda s, e: kpis(s, e)["mrr"],
    "nrr": lambda s, e: kpis(s, e)["nrr"],
    "active_accounts": lambda s, e: kpis(s, e)["accounts"],
    "churn_rate": lambda s, e: churn_delta(end=e)["current"],
    "data_quality": lambda s, e: run_checks(s, e),
    "gross_churned_arr": lambda s, e: gross_churned_arr(s, e),
    "active_accounts_eom": lambda s, e: active_accounts_eom(e),
}


def metric_value(name, start, end=None):
    """Compiled registry value for metric `name` over [start, end or
    start]. Unknown names raise ValueError (quoted) before any SQL runs.
    Mirrors queries.metric_value."""
    if name not in _PG_METRIC_FUNCS:
        raise ValueError(
            f"metric {name!r} is not whitelisted; "
            f"must be one of {tuple(_PG_METRIC_FUNCS)}"
        )
    if end is None:
        end = start
    return _PG_METRIC_FUNCS[name](start, end)


def profile():
    """The datasets-profile shape for the Postgres source: `{"tables",
    "rows", "data_end"}` where `tables` lists only the known event/account
    tables actually present (sorted), `rows` sums their row counts, and
    `data_end` is the union-MAX of `event_date` over the event tables
    present (None when no event date exists). Opens its own read-only
    connection; never raises — an unreachable database profiles empty, so
    `GET /data/datasets` stays JSON-shaped."""
    empty = {"tables": [], "rows": 0, "data_end": None}
    try:
        source = PostgresSource()
        tables = source.tables()
    except Exception:  # noqa: BLE001 - profiling must never raise
        return dict(empty)
    rows = 0
    for table in tables:
        try:
            conn = source.connect_ro()
        except Exception:  # noqa: BLE001 - unreachable database reads empty
            return dict(empty)
        try:
            # Table names come from _KNOWN_TABLES via information_schema,
            # never from caller input, so quoting is structural, not
            # interpolation of values.
            rows += conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except Exception:  # noqa: BLE001 - a vanishing table reads as absent
            continue
        finally:
            conn.close()
    data_end = None
    if any(name in tables for name in _EVENT_TABLES):
        try:
            data_end = source.latest_event_day()
        except Exception:  # noqa: BLE001 - no dates read as None
            data_end = None
    return {"tables": tables, "rows": rows, "data_end": data_end}


class PostgresSource(Source):
    """The `Source` protocol over a Postgres database (issue #80).

    The connection string comes from `DATASOURCE_URL`; use `use_postgres()`
    to test whether this backend is selected. Every query method below
    delegates to the module-level function of the same name (identical
    validation and return shapes to the SQLite path), so this class offers
    the same method surface both structurally (via `Source`) and
    behaviorally (via the query functions).
    """

    def connect_ro(self):
        """Open the Postgres database read-only via `_connect()`.

        Least-privilege by construction (`default_transaction_read_only=on`
        plus a 5s statement timeout, autocommit so probes never hold a
        transaction open). The caller closes the connection (usually in
        `finally`)."""
        return _connect()

    def tables(self):
        """The known event/account tables actually present, sorted.

        Reads `information_schema` over its own read-only connection (closed
        in `finally`) and reports only the generator's four tables that are
        really there, sorted."""
        conn = self.connect_ro()
        try:
            rows = conn.execute(_PG_SQL_TABLES, (list(_KNOWN_TABLES),)).fetchall()
        finally:
            conn.close()
        return sorted(row[0] for row in rows if row[0] in _KNOWN_TABLES)

    def latest_event_day(self):
        """Latest observed event day as an ISO string, or None.

        Runs the union-`MAX(event_date)` template over its own read-only
        connection (closed in `finally`): the data's own horizon, never a
        wall clock; None on a database with no events."""
        conn = self.connect_ro()
        try:
            return conn.execute(_PG_SQL_LATEST_EVENT_DATE).fetchone()[0]
        finally:
            conn.close()

    def kpis(self, start, end):
        """Monthly KPI block; see the module-level `kpis`."""
        return kpis(start, end)

    def arr_series(self, start, end):
        """Daily ARR series; see the module-level `arr_series`."""
        return arr_series(start, end)

    def churn_cohort(self, window, limit=None):
        """Churn cohort rows; see the module-level `churn_cohort`."""
        return churn_cohort(window, limit=limit)

    def churn_driver(self, window, data_end=None, limit=None):
        """Churn-spike facts; see the module-level `churn_driver`."""
        return churn_driver(window, data_end=data_end, limit=limit)

    def churn_cohort_detail(self, window, segment=None, limit=None):
        """Row-level churn cohort; see the module-level function."""
        return churn_cohort_detail(window, segment=segment, limit=limit)

    def churn_delta(self, end, metric="churn_rate", baseline_months=6):
        """Churn variance; see the module-level `churn_delta`."""
        return churn_delta(end, metric=metric, baseline_months=baseline_months)

    def run_checks(self, start=None, end=None, *, window=None):
        """Data-quality checks; see the module-level `run_checks`."""
        return run_checks(start, end, window=window)

    def recent_problem_scope(self, lookback_days=_PROBLEM_LOOKBACK_DAYS):
        """Dirty-window detection; see the module-level function."""
        return recent_problem_scope(lookback_days=lookback_days)

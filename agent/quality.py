"""Data-quality checks over the seeded SQLite database (issue #6).

run_checks returns exactly three check results in the fixed order
freshness, duplicates, nulls — each {"name", "status", "score"} where
status is "ok" | "warn" | "fail" and score is the confidence that the data
is CLEAN for that check (1.0 = perfectly clean/fresh, 0.0 = maximally
broken), a float rounded to 4 decimals. Every row is JSON-serializable by
construction, ready to slot straight into the data_quality_card and the
"Is the data clean?" Noul questions.

Check definitions over the inclusive date scope [start, end] (the scope
resolves to the trailing DEFAULT_SCOPE_DAYS ending at the latest observed
event date when no bounds are passed — the data's own latest day, never a
wall clock, since the seed ends 2026-09-30 and an unscoped probe must stay
deterministic):

  * freshness := share of the scope's calendar days on which at least one
    event of any kind exists (union of distinct event_dates across
    subscription_events / revenue_events / usage_events). ok when every
    day is covered; warn when 1-3 days are missing; fail when more than 3
    days are missing or the scope contains no events at all (an empty
    scope is the extreme of stale — it is also the fix for not reading the
    absence of data as "nothing to report").
  * duplicates := share of usage_events rows in scope that are redundant
    copies — SUM(n - 1) over (account_id, event_date, metric, units)
    groups with n > 1 (the generator's duplicate definition: same account,
    day, metric and units), divided by all in-scope rows. The September
    ingest bug's exact-duplicate plants sit near 6% in-window; fail above
    1%, warn above 0.2%.
  * nulls := share of usage_events rows in scope with units IS NULL — the
    ingest bug's second plant; the PRD's breach line is a strict null% > 2%
    (fail), warn above 0.1%.

Zero-row scopes make the two share checks vacuously clean (score 1.0, ok)
— shares measure corruption of existing rows while freshness measures
existence — so a window before the data begins reads ok / ok / fail rather
than raising three alarms.

Scope resolution: run_checks(), run_checks("2026-09-08", "2026-09-21")
and run_checks(window=("2026-09-08", "2026-09-21")) are equivalent forms;
`window` takes a (start, end) pair and combining it with either `start` or
`end` is ValueError. With only `start` given, `end` is the latest observed
event date; with only `end` (or with neither), `start` is
DEFAULT_SCOPE_DAYS - 1 days before `end`; on an eventless DB `end` falls
back to today, where every check degenerates to its empty-scope reading.

Problem scoping (issue #17): recent_problem_scope() detects WHERE the data
is actually dirty rather than trusting a display window — it scans the
trailing _PROBLEM_LOOKBACK_DAYS (ending at the latest observed event day,
like run_checks' anchor) for usage rows that are exact-duplicate extras or
NULL-units (precisely the duplicates/nulls checks' numerators, tallied per
day and segment), clips them to the contiguous calendar-day run ending at
the latest dirty day, and reports that run with the majority segment of its
dirty rows (ties break alphabetically). On the seeded DB that is exactly
2026-09-08..2026-09-21, Startup — the Sept ingest bug — so "Is the data
clean?" is read where the corruption lives instead of over a diluting
trailing-30 window; a clean or eventless DB reads None and the caller keeps
its own scoping.

Security model mirrors queries.py: every SQL statement is a static
module-level template; values are interpolated NEVER — they travel only as
bound ? parameters, and caller-supplied date strings are canonicalized via
date.fromisoformat (ValueError on anything else, injection payloads
included) before any SQL runs. Connections come only from the `Source` protocol (`SqliteSource`,
`agent/data/`): this module never writes, mutates DDL, or even
opens a write handle. Stdlib only, mirroring db.py — importing this module
never touches the agent's model dependencies.

The input validators below are local copies of queries.py's private
_require_date / _require_window / _require_window_tuple (identical
contract, kept under the scope-flavored name _require_scope_pair) so the
diff stays additive-only; sharing one validators module is deliberately
deferred.
"""

from datetime import date, timedelta

try:  # package-style entry (import agent.quality from the repo root)
    from agent.data import SqliteSource
    from agent.data.protocol import Source
except ModuleNotFoundError:  # script-style entry (import quality from agent/)
    from data import SqliteSource
    from data.protocol import Source

# Default scope for the no-arg call: the N trailing days ending at the
# latest observed event date. Trailing-window (not full-range) so freshness
# stays meaningful unscoped — a full-range default would end at the latest
# day by construction and read ok forever regardless of recent gaps.
DEFAULT_SCOPE_DAYS = 30

# Freshness band: 1-3 uncovered calendar days lag quietly (warn); anything
# beyond that, or a scope with no events at all, is broken (fail) — the
# seeded bug week itself must still read ok, so the warn band cannot start
# at 1 missing day.
_FRESHNESS_MAX_WARN_GAP_DAYS = 3

# Duplicates band: sub-0.2% redundant rows is benign re-ingest noise; above
# that warns; the Sept bug's exact-copy plants (~6% in-window) must fail,
# so the fail line sits far below at 1%.
_DUPLICATE_FAIL_SHARE = 0.01
_DUPLICATE_WARN_SHARE = 0.002

# Nulls band: the PRD's literal breach line is null units > 2% of rows
# (strict, so exactly 2.0% still warns); up to 0.1% is transient-pipeline
# noise, between it and the breach line warns.
_NULL_FAIL_SHARE = 0.02
_NULL_WARN_SHARE = 0.001

# Fixed result order — consumers index the list positionally.
_CHECK_NAMES = ("freshness", "duplicates", "nulls")

# Latest event day across all three event tables: per-table MAX rides the
# event_date indexes, the outer MAX folds the three candidates.
_SQL_LATEST_EVENT_DATE = """
SELECT MAX(d) FROM (
    SELECT MAX(event_date) AS d FROM subscription_events
    UNION ALL
    SELECT MAX(event_date) FROM revenue_events
    UNION ALL
    SELECT MAX(event_date) FROM usage_events
)
"""

# Distinct event days covered inside the scope, over the union of the three
# event tables. Params: (start, end) x3.
_SQL_COVERED_DAYS = """
SELECT COUNT(DISTINCT d) FROM (
    SELECT event_date AS d FROM subscription_events WHERE event_date BETWEEN ? AND ?
    UNION
    SELECT event_date FROM revenue_events WHERE event_date BETWEEN ? AND ?
    UNION
    SELECT event_date FROM usage_events WHERE event_date BETWEEN ? AND ?
)
"""

# (redundant rows, total rows) for usage in scope: redundant = SUM(n - 1)
# over exact-tuple groups with more than one row. Params: (start, end) x2.
_SQL_DUPLICATE_SHARE = """
SELECT
    (SELECT COALESCE(SUM(n - 1), 0) FROM (
        SELECT COUNT(*) AS n FROM usage_events
        WHERE event_date BETWEEN ? AND ?
        GROUP BY account_id, event_date, metric, units
        HAVING n > 1
    )),
    (SELECT COUNT(*) FROM usage_events WHERE event_date BETWEEN ? AND ?)
"""

# (null-units rows, total rows) for usage in scope. Params: (start, end) x2.
_SQL_NULL_SHARE = """
SELECT
    (SELECT COUNT(*) FROM usage_events
     WHERE event_date BETWEEN ? AND ? AND units IS NULL),
    (SELECT COUNT(*) FROM usage_events WHERE event_date BETWEEN ? AND ?)
"""

# Issue #17 problem detection: how far back the corruption scan reaches —
# the Sept plant ends 9 days before the seed's 2026-09-30 anchor, so 120
# days leave margin for the plant drifting with future regenerations.
_PROBLEM_LOOKBACK_DAYS = 120

# Dirty usage rows per (event_date, segment): exact-duplicate EXTRAS only —
# SUM(n - 1) over the same (account_id, event_date, metric, units) groups
# _SQL_DUPLICATE_SHARE counts — joined to accounts for the segment (each
# dup group sits entirely on one account, so the segment is unambiguous).
# Params: (start, end).
_SQL_PROBLEM_DUP_ROWS = """
SELECT event_date AS d, segment, SUM(n - 1) AS extras
FROM (
    SELECT ue.account_id, ue.event_date, ue.metric, ue.units,
           a.segment AS segment, COUNT(*) AS n
    FROM usage_events ue
    JOIN accounts a ON a.id = ue.account_id
    WHERE ue.event_date BETWEEN ? AND ?
    GROUP BY ue.account_id, ue.event_date, ue.metric, ue.units, a.segment
    HAVING COUNT(*) > 1
)
GROUP BY event_date, segment
"""

# Dirty usage rows per (event_date, segment): NULL-units rows, mirroring
# _SQL_NULL_SHARE's numerator. Params: (start, end).
_SQL_PROBLEM_NULL_ROWS = """
SELECT ue.event_date AS d, a.segment AS segment, COUNT(*) AS n
FROM usage_events ue
JOIN accounts a ON a.id = ue.account_id
WHERE ue.event_date BETWEEN ? AND ?
  AND ue.units IS NULL
GROUP BY ue.event_date, a.segment
"""


def _require_date(value, label):
    """Validate `value` as a calendar date, returning the canonical
    'YYYY-MM-DD' form. ValueError on anything else — including SQL
    injection payloads, which never resemble a date — so only this
    canonical string is ever bound into SQL. Local copy of
    queries._require_date (identical contract)."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO 'YYYY-MM-DD' date string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{label} is not a valid ISO date: {value!r}") from None


def _require_window(start, end):
    """Both bounds valid and ordered; returns the canonical pair. Local copy
    of queries._require_window (identical contract)."""
    start = _require_date(start, "window start")
    end = _require_date(end, "window end")
    if start > end:
        raise ValueError(f"window start {start} is after window end {end}")
    return start, end


def _require_scope_pair(window):
    """A scope pair must arrive as an exact (start, end) pair; strings and
    other lengths are rejected before any date parsing. Same semantics as
    queries._require_window_tuple, named for the DQ scope it validates."""
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


def _latest_event_date(conn):
    """Latest event day across all three event tables, or None when the DB
    holds no events."""
    return conn.execute(_SQL_LATEST_EVENT_DATE).fetchone()[0]


def _freshness(conn, start, end):
    """(score, status): covered calendar days / window days, banded by the
    count of missing days — 0 is ok, 1..3 warns, more than that (or a fully
    empty scope) fails."""
    covered_days = conn.execute(
        _SQL_COVERED_DAYS, (start, end, start, end, start, end)
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
    """(score, status): 1 - redundant-rows share of usage in scope, banded
    by _DUPLICATE_* thresholds. A zero-row scope is vacuously clean — there
    is nothing to corrupt."""
    redundant_rows, total_rows = conn.execute(
        _SQL_DUPLICATE_SHARE, (start, end, start, end)
    ).fetchone()
    share = redundant_rows / total_rows if total_rows else 0.0
    return 1.0 - share, _share_status(share, _DUPLICATE_FAIL_SHARE, _DUPLICATE_WARN_SHARE)


def _nulls(conn, start, end):
    """(score, status): 1 - NULL-units share of usage in scope, banded by
    the _NULL_* thresholds (fail line is the PRD's strict null% > 2%)."""
    null_rows, total_rows = conn.execute(
        _SQL_NULL_SHARE, (start, end, start, end)
    ).fetchone()
    share = null_rows / total_rows if total_rows else 0.0
    return 1.0 - share, _share_status(share, _NULL_FAIL_SHARE, _NULL_WARN_SHARE)


def _result(name, score, status):
    """Clamp the raw metric into [0, 1], round to 4 decimals and coerce to
    float — the fixed row shape [{name, status, score}] consumers get."""
    clamped = min(1.0, max(0.0, float(score)))
    return {"name": name, "status": status, "score": float(round(clamped, 4))}


def run_checks(start=None, end=None, *, window=None):
    """Run the three data-quality checks and return them in the fixed order
    freshness, duplicates, nulls — [{name, status, score}], see the module
    docstring for the check semantics, thresholds and scope resolution."""
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

    # All reads go through the Source protocol — the connection below is
    # a read-only SqliteSource handle, never a direct db.open — so a
    # Postgres Source can slot in without touching this logic.
    source: Source = SqliteSource()
    conn = source.connect_ro()
    try:
        if end is None:
            # Anchor the default scope on the data's own latest day — never
            # a wall clock, so unscoped probes are deterministic on the
            # seeded DB; an eventless DB falls back to today, where every
            # check degenerates to its empty-scope reading.
            end = _latest_event_date(conn) or date.today().isoformat()
        if start is None:
            start = (
                date.fromisoformat(end) - timedelta(days=DEFAULT_SCOPE_DAYS - 1)
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
    from the detection scan's own day strings, cloning _require_date's
    validation so a non-ISO day raises ValueError before any range math.
    Clipping to the contiguous tail keeps the reported window anchored on
    the LIVE problem: dirty days that a clean day separates from the tail
    are older dirt, not the scope this module claims."""
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
    """Detect the recent window where the data is actually dirty (issue #17).

    Scans the trailing `lookback_days` (ending at the latest observed event
    day, never a wall clock) for usage rows that are exact-duplicate extras
    or NULL-units — the duplicates/nulls checks' own numerators, tallied
    per (day, segment) — then clips to the contiguous dirty-day run ending
    at the latest dirty day and takes that run's majority segment (most
    dirty rows first, alphabetical on ties). Returns
    {"start", "end", "segment"} or None when the DB is clean or eventless;
    ValueError on a non-positive or non-integer lookback_days (bools read
    as the ints they subclass, so they are rejected explicitly). Read-only
    over usage_events + accounts, like every query in this module."""
    if (
        isinstance(lookback_days, bool)
        or not isinstance(lookback_days, int)
        or lookback_days < 1
    ):
        raise ValueError(
            f"lookback_days must be a positive integer, got {lookback_days!r}"
        )
    # Same protocol seam as run_checks: the scan reads through a
    # read-only SqliteSource handle, never a direct db.open.
    source: Source = SqliteSource()
    conn = source.connect_ro()
    try:
        end = _latest_event_date(conn)
        if not end:
            # Eventless DB: no anchor day, nothing to scope — the caller
            # falls back to its own window (run_checks semantics).
            return None
        start = (
            date.fromisoformat(end) - timedelta(days=lookback_days - 1)
        ).isoformat()
        # (day, segment) -> dirty-row count: duplicate extras + NULL-units
        # rows, i.e. exactly the rows the two share checks would penalize.
        dirty = {}
        for day, segment, extras in conn.execute(
            _SQL_PROBLEM_DUP_ROWS, (start, end)
        ):
            dirty[(day, segment)] = dirty.get((day, segment), 0) + (extras or 0)
        for day, segment, rows in conn.execute(
            _SQL_PROBLEM_NULL_ROWS, (start, end)
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

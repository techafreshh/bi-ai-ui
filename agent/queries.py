"""Read-only SQL query layer over the seeded SQLite database (issue #5).

The four functions below are the data contract the graph nodes (issue #6+)
fill UI components from: monthly KPIs, a daily ARR series, the churn cohort
table, and churn baseline/delta versus a trailing baseline. Everything
returned is JSON-serializable by construction — dollars for money keys,
floats for ratios, ints for counts — with dates as 'YYYY-MM-DD' strings.

Metric definitions (all windows inclusive; "active on day D" means the
account has a 'new' event <= D and no 'cancel' event <= D, and no event
after D is ever counted — point-in-time, never leaked):

  * net_new_arr(start..end) := SUM(arr_delta_cents) over subscription_events
    in the window — the generator's own definition
    (scripts/generate_data.py docstring); the zero-delta 'cancel' markers
    contribute nothing.
  * mrr(end) := active ARR at `end` / 12, where an account's ARR counts only
    its events <= `end` — upgrades after the window never leak in.
  * accounts(end) := active-account count at `end`.
  * nrr(start, end) := cohort ARR at `end` / cohort ARR at `start`, where
    the cohort is accounts with a 'new' event <= `start`; members churned
    by `start` or by `end` contribute 0 to the respective sum. A zero
    denominator yields 0.0.
  * arr_series(start, end): one row per calendar day; `arr` = total active
    ARR that day in dollars (0.0 on days before any account existed).
  * churn_cohort(window): one row per 'cancel' event in the window, ordered
    by (event_date, account_id), at most `limit` rows. `mrr` = churn-time
    ARR / 12 — the accounts row keeps its churn-time plan/ARR by the
    generator's design (cancel is the account's last event), so
    accounts.arr_cents needs no replay. `tenure_months` = days from
    created_at to the cancel date / 30.4375, rounded to 1 decimal.
  * churn_driver(window, data_end, limit): the churn-spike facts behind the
    variance callout's cause and the Investigate drill-down (issue #16).
    The cohort window is (window.start, data_end) ONLY when the user's own
    window already contains at least one repriced cancel (a 'cancel' in the
    window with an 'upgrade' for the same account in
    [window.start, cancel_date] — the data's repriced-churn signature);
    otherwise the cohort stays (window.start, window.end), so an off-spike
    question never lists cancels it did not ask about. Returned dict:
    {cohort_start, cohort_end, spiked, cancelled, repriced, rows,
    dominant_segment, dominant_share} — counts from one aggregate SQL over
    the cohort window, `rows` reused from churn_cohort, the segment shares
    computed in Python over those <= limit rows.
  * churn_cohort_detail(window, segment, limit): the drill-down's row-level
    cohort — one {segment, plan, mrr, tenure_months, cancelled_on,
    repriced} row per cancel event (`repriced` the human-readable
    'yes'/'no' form of the same repricing signature as churn_driver),
    optionally filtered to one segment.
  * churn_delta(end, baseline_months): churn rate of a window W :=
    churned ARR in W (the churning accounts' accounts.arr_cents summed over
    'cancel' events in W) / active ARR at W's first day; a churn ON that
    first day sits in the numerator but not the instant denominator (the
    seed has no first-day churn). The current window is
    [first-of-month(end), end]; the baseline is the mean monthly rate over
    the baseline_months full calendar months preceding that month. Delta is
    RELATIVE — (current - baseline) / baseline — because monthly revenue
    churn here is ~0.1-0.7% of ARR, so absolute rate differences top out at
    ~0.007 and only the relative reading is discriminating (the >0.2
    acceptance clause). Windows with no active ARR (before the data range)
    rate 0.0; a baseline of exactly 0.0 yields delta 1.0 if current > 0
    else 0.0.

Security model (why this module exists): every SQL statement is a static
module-level template; values are interpolated NEVER — they travel only as
bound ? / :name parameters or as canonicalized ISO dates validated via
date.fromisoformat before any SQL runs. Connections come only from
db.connect(readonly=True) (mode=ro URI): this module never writes, mutates
DDL, or even opens a write handle. Malformed input raises ValueError before
a connection is opened.

Stdlib only, mirroring db.py: importing this module never touches the
agent's model dependencies.
"""

import calendar
from datetime import date

try:  # package-style entry (import agent.queries from the repo root)
    from agent import db
except ModuleNotFoundError:  # script-style entry (import queries from agent/)
    import db

try:  # package-style entry (import agent.queries from the repo root)
    from agent.semantics import load_metrics
except ModuleNotFoundError:  # script-style entry (import queries from agent/)
    from semantics import load_metrics

# Metric registry compiled at import (issue #77): the YAML file's typos
# raise here — at startup, before any query runs — so the server refuses
# to boot on a bad file instead of serving queries against it.
METRICS = load_metrics()

# The only metric churn_delta accepts; anything else is ValueError.
_CHURN_METRICS = ("churn_rate",)

# Average month length in days (365.25 / 12) for tenure in months.
_DAYS_PER_MONTH = 30.4375

# Net-new ARR in a window: every event type participates (cancel markers
# carry arr_delta_cents = 0). Params: (start, end).
_SQL_NET_NEW_ARR = """
SELECT COALESCE(SUM(arr_delta_cents), 0)
FROM subscription_events
WHERE event_date BETWEEN ? AND ?
"""

# Point-in-time state on a day: per account, the ARR of its events <= day
# (cancel rows carry a 0 delta, so the sum is unaffected by them) plus a
# churned-by-day flag; the outer WHERE keeps only still-active accounts.
# One grouped scan instead of per-account queries — the events table has no
# account_id index. Params: (day,).
_SQL_ACTIVE_DAY = """
SELECT COALESCE(SUM(cents), 0) AS active_arr_cents,
       COUNT(*) AS active_accounts
FROM (
    SELECT SUM(arr_delta_cents) AS cents,
           MAX(CASE WHEN event_type = 'cancel' THEN 1 ELSE 0 END) AS churned
    FROM subscription_events
    WHERE event_date <= ?
    GROUP BY account_id
)
WHERE churned = 0
"""

# NRR cohort replay: the cohort — accounts with a 'new' event <= :start —
# priced at :start (members already churned by :start read 0) versus at
# :end (members churned by :end read 0). Params: {"start", "end"}.
_SQL_NRR = """
WITH cohort AS (
    SELECT DISTINCT account_id
    FROM subscription_events
    WHERE event_type = 'new' AND event_date <= :start
),
per_account AS (
    SELECT s.account_id,
           MAX(CASE WHEN s.event_date <= :start AND s.event_type = 'cancel'
                    THEN 1 ELSE 0 END) AS churned_at_start,
           MAX(CASE WHEN s.event_date <= :end AND s.event_type = 'cancel'
                    THEN 1 ELSE 0 END) AS churned_at_end,
           COALESCE(SUM(CASE WHEN s.event_date <= :start
                             THEN s.arr_delta_cents ELSE 0 END), 0) AS arr_at_start,
           COALESCE(SUM(CASE WHEN s.event_date <= :end
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

# Daily ARR replay in one pass: calendar is a recursive CTE over the window;
# `windowed` computes, per account, the running ARR after each event and the
# day the NEXT event changes it (LEAD over ALL event types); `moves` keeps
# only non-cancel rows, so each becomes the interval [event_day, next_day)
# and a cancel ends its account BEFORE its own day (no interval is built
# from it, and the prior interval stops at its LEAD). Rows starting at or
# after the account's churn are also dropped — the seed contains post-cancel
# upgrade markers (repricings not clamped to the cancel date), which would
# otherwise resurrect the account past its cancel. Filtering dates before
# the window computation is what keeps post-window upgrades out of running
# sums. LEFT JOIN keeps calendar days no interval covers at 0.0.
# NOTE on param order: ?1 = start (calendar anchor), ?2 = end (calendar
# bound), ?3 = end again (event filter) — hence the (start, end, end) tuple.
_SQL_ARR_SERIES = """
WITH RECURSIVE calendar(day) AS (
    SELECT ?
    UNION ALL
    SELECT date(day, '+1 day') FROM calendar WHERE day < ?
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
    WHERE event_date <= ?
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
# (accounts.arr_cents, see module docstring) by 12 and tenure divides the
# exact day count by 30.4375 (julianday of midnight-ISO strings differs by
# whole days). Params: (window_start, window_end, limit).
_SQL_CHURN_COHORT = """
SELECT a.segment AS segment,
       ROUND(a.arr_cents / 1200.0, 2) AS mrr,
       ROUND((julianday(s.event_date) - julianday(a.created_at)) / 30.4375, 1)
           AS tenure_months,
       a.plan AS plan
FROM subscription_events s
JOIN accounts a ON a.id = s.account_id
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN ? AND ?
ORDER BY s.event_date, s.account_id
LIMIT ?
"""

# Churn-spike counts over a window (issue #16): every cancel in
# [:start, :end] plus — per row — whether the SAME account has an 'upgrade'
# (the repricing event of the planted pricing change) in
# [:paired_from, cancel_date]. The repriced share of the cancelled rows is
# the data-grounded fact the cause string claims; a window whose cancels
# were never preceded by a repricing reads repriced = 0 and gets no cause.
# Params: {"start", "end", "paired_from"}.
_SQL_CHURN_REPRICED = """
SELECT COUNT(*) AS cancelled,
       SUM(CASE WHEN EXISTS (
               SELECT 1 FROM subscription_events u
               WHERE u.account_id = s.account_id
                 AND u.event_type = 'upgrade'
                 AND u.event_date >= :paired_from
                 AND u.event_date <= s.event_date
           ) THEN 1 ELSE 0 END) AS repriced
FROM subscription_events s
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN :start AND :end
"""

# The drill-down's row-level cohort (issue #16): _SQL_CHURN_COHORT plus the
# cancel date itself, the churn-time plan and the repricing flag (same
# signature as _SQL_CHURN_REPRICED, evaluated against the cohort window's
# start). `:segment` is bound as None for "all segments" — a NULL parameter
# short-circuits the OR, so one static template serves both shapes. Params:
# {"start", "end", "paired_from", "segment", "limit"}.
_SQL_CHURN_COHORT_DETAIL = """
SELECT a.segment AS segment,
       a.plan AS plan,
       ROUND(a.arr_cents / 1200.0, 2) AS mrr,
       ROUND((julianday(s.event_date) - julianday(a.created_at)) / 30.4375, 1)
           AS tenure_months,
       s.event_date AS cancelled_on,
       CASE WHEN EXISTS (
               SELECT 1 FROM subscription_events u
               WHERE u.account_id = s.account_id
                 AND u.event_type = 'upgrade'
                 AND u.event_date >= :paired_from
                 AND u.event_date <= s.event_date
           ) THEN 'yes' ELSE 'no' END AS repriced
FROM subscription_events s
JOIN accounts a ON a.id = s.account_id
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN :start AND :end
  AND (:segment IS NULL OR a.segment = :segment)
ORDER BY s.event_date, s.account_id
LIMIT :limit
"""

# Churned ARR in a window: the churn-time ARR of every cancelling account
# (one cancel event per account by construction). Params: (start, end).
_SQL_CHURNED_ARR = """
SELECT COALESCE(SUM(a.arr_cents), 0)
FROM subscription_events s
JOIN accounts a ON a.id = s.account_id
WHERE s.event_type = 'cancel'
  AND s.event_date BETWEEN ? AND ?
"""

# Latest event day across all three event tables: per-table MAX rides the
# event_date indexes, the outer MAX folds the three candidates (the same
# union-MAX shape quality.py uses for its default scope). Params: none.
_SQL_LATEST_EVENT_DATE = """
SELECT MAX(d) FROM (
    SELECT MAX(event_date) AS d FROM subscription_events
    UNION ALL
    SELECT MAX(event_date) FROM revenue_events
    UNION ALL
    SELECT MAX(event_date) FROM usage_events
)
"""


def _require_date(value, label):
    """Validate `value` as a calendar date, returning the canonical
    'YYYY-MM-DD' form. ValueError on anything else — including SQL
    injection payloads, which never resemble a date — so only this
    canonical string is ever bound into SQL (canonicalization also forbids
    other fromisoformat-accepted formats like '20260701', which would break
    the TEXT 'YYYY-MM-DD' BETWEEN comparisons)."""
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
    row = conn.execute(_SQL_ACTIVE_DAY, (day,)).fetchone()
    return row[0], row[1]


def _churn_rate(conn, window_start, window_end):
    """churn rate of [window_start, window_end]: churned ARR in the window
    divided by active ARR at the window's first day (0.0 when there was
    nothing active — e.g. baseline months before the data range)."""
    churned_cents = conn.execute(
        _SQL_CHURNED_ARR, (window_start, window_end)
    ).fetchone()[0]
    active_cents = _active_state(conn, window_start)[0]
    if active_cents <= 0:
        return 0.0
    return churned_cents / active_cents


def kpis(start, end):
    """Monthly KPI block over the inclusive window: net_new_arr (dollars,
    the window's SUM of arr_delta_cents), mrr (active ARR at `end` / 12),
    nrr (cohort ARR at `end` / at `start`, 0.0 on a zero base) and accounts
    (active count at `end`). Month-over-month net-new comparisons come from
    calling this once per month window."""
    start, end = _require_window(start, end)
    conn = db.connect(readonly=True)
    try:
        net_new_cents = conn.execute(_SQL_NET_NEW_ARR, (start, end)).fetchone()[0]
        active_arr_cents, active_accounts = _active_state(conn, end)
        cohort_arr_at_start, cohort_arr_at_end = conn.execute(
            _SQL_NRR, {"start": start, "end": end}
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
    """Gross churned ARR in dollars over the inclusive window: the
    churn-time ARR of every cancelling account summed via _SQL_CHURNED_ARR
    (issue #77's file-only metric — no new SQL, the same template the
    churn rate's numerator uses, without the active-base division)."""
    start, end = _require_window(start, end)
    conn = db.connect(readonly=True)
    try:
        churned_cents = conn.execute(
            _SQL_CHURNED_ARR, (start, end)
        ).fetchone()[0]
    finally:
        conn.close()
    return round(churned_cents / 100, 2)


def active_accounts_eom(end):
    """Active-account count at the window end day via _SQL_ACTIVE_DAY
    (issue #77's file-only metric — no new SQL, the same point-in-time
    template mrr/accounts read, count half only)."""
    day = _require_date(end, "end")
    conn = db.connect(readonly=True)
    try:
        row = conn.execute(_SQL_ACTIVE_DAY, (day,)).fetchone()
    finally:
        conn.close()
    return row[1]


def arr_series(start, end):
    """One {"date", "arr"} row per calendar day from `start` to `end`
    inclusive, `arr` = total active ARR that day in dollars (0.0 on uncovered
    days), replayed from the events table in a single query."""
    start, end = _require_window(start, end)
    conn = db.connect(readonly=True)
    try:
        rows = conn.execute(_SQL_ARR_SERIES, (start, end, end)).fetchall()
    finally:
        conn.close()
    return [{"date": row[0], "arr": row[1]} for row in rows]


def latest_event_date():
    """Latest observed event day across all three event tables as an ISO
    string, or None on a DB with no events at all — the deterministic
    "data_end" anchor read_state resolves query windows against (the data's
    own horizon, never a wall clock). Read-only, like every query here."""
    conn = db.connect(readonly=True)
    try:
        return conn.execute(_SQL_LATEST_EVENT_DATE).fetchone()[0]
    finally:
        conn.close()


def churn_cohort(window, limit=50):
    """Churn cohort rows for the inclusive `window` (a (start, end) pair):
    one {"segment", "mrr", "tenure_months", "plan"} row per cancel event,
    ordered by (event_date, account_id), at most `limit` rows (>= 1)."""
    start, end = _require_window_tuple(window)
    _require_int_at_least(limit, "limit", 1)
    conn = db.connect(readonly=True)
    try:
        rows = conn.execute(_SQL_CHURN_COHORT, (start, end, limit)).fetchall()
    finally:
        conn.close()
    return [
        {
            "segment": row[0],
            "mrr": row[1],
            "tenure_months": row[2],
            "plan": row[3],
        }
        for row in rows
    ]


def _repriced_counts(conn, start, end, paired_from):
    """(cancelled, repriced) over the inclusive window: every 'cancel' in
    [start, end], of those the rows whose account also has an 'upgrade' in
    [paired_from, cancel_date]. One aggregate scan (see _SQL_CHURN_REPRICED)."""
    cancelled, repriced = conn.execute(
        _SQL_CHURN_REPRICED,
        {"start": start, "end": end, "paired_from": paired_from},
    ).fetchone()
    # SUM over zero rows reads NULL, not 0 — the cancelled==0 case.
    return cancelled, (repriced or 0)


def _dominant_segment(rows):
    """(segment, share) with the highest row count among the cohort `rows`
    the table actually shows (ties broken by count, then lexicographically,
    so the pick is deterministic whatever the segment mix); share is relative
    to len(rows), rounded to 4 decimals, JSON-clean. No rows: (None, 0.0)."""
    if not rows:
        return None, 0.0
    counts = {}
    for row in rows:
        segment = row["segment"]
        counts[segment] = counts.get(segment, 0) + 1
    segment, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return segment, round(count / len(rows), 4)


def churn_driver(window, data_end=None, limit=50):
    """Churn-spike facts for the variance callout and the Investigate
    drill-down (issue #16) over `window` (a (start, end) pair of ISO dates).

    The spike signature is a repriced cancel: a 'cancel' in the window whose
    account also has an 'upgrade' in [window.start, cancel_date] (the paired
    repricing of the planted pricing change). When the user's own window
    shows that signature (`spiked`), the cohort window extends from
    window.start to `data_end` (the data's latest event day) when data_end
    sits beyond the window, so the WHOLE spike cohort is tabled, not only
    the part inside the literal window the user named; otherwise — no
    repriced cancel, or no data_end beyond the window — the cohort stays
    window-scoped and an off-spike question lists only what it asked for.

    Returns {"cohort_start", "cohort_end", "spiked", "cancelled",
    "repriced", "rows", "dominant_segment", "dominant_share"}: rows reused
    from churn_cohort (at most `limit` >= 1), counts from aggregate SQL over
    the cohort window, and the dominant segment/share computed over exactly
    those visible rows (so the callout's claim never exceeds the table).
    `data_end` must be None or an ISO date; anything else is ValueError
    before any SQL runs."""
    start, end = _require_window_tuple(window)
    if data_end is not None:
        data_end = _require_date(data_end, "data_end")
    _require_int_at_least(limit, "limit", 1)

    conn = db.connect(readonly=True)
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


def churn_cohort_detail(window, segment=None, limit=50):
    """Row-level churn cohort for the Investigate drill-down (issue #16):
    one {"segment", "plan", "mrr", "tenure_months", "cancelled_on",
    "repriced"} dict per cancel event in the inclusive `window` (a (start,
    end) pair of ISO dates), ordered by (event_date, account_id), at most
    `limit` rows (>= 1). `segment` None means every segment; a value is
    matched exactly (parameterized — never interpolated). `repriced` is the
    portal-facing 'yes'/'no' spelling of churn_driver's repricing signature
    (an 'upgrade' in [window.start, cancel_date]), because table cells render
    via String(value) and a bare 0/1 would read as '0'."""
    start, end = _require_window_tuple(window)
    if segment is not None:
        if not isinstance(segment, str) or not segment:
            raise ValueError("segment must be None or a non-empty string")
    _require_int_at_least(limit, "limit", 1)
    conn = db.connect(readonly=True)
    try:
        rows = conn.execute(
            _SQL_CHURN_COHORT_DETAIL,
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
            "mrr": row[2],
            "tenure_months": row[3],
            "cancelled_on": row[4],
            "repriced": row[5],
        }
        for row in rows
    ]


def churn_delta(end, metric="churn_rate", baseline_months=6):
    """Churn variance at `end` against a trailing baseline: {"delta",
    "baseline", "current"} where `current` is the month-to-date churn rate
    [first-of-month(end), end], `baseline` the mean monthly churn rate over
    the `baseline_months` (>= 1) full calendar months preceding that month,
    and `delta` the RELATIVE change (current - baseline) / baseline with the
    zero-baseline convention 1.0 if current > 0 else 0.0. `metric` is
    whitelist-checked ("churn_rate" only)."""
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
    conn = db.connect(readonly=True)
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


def _metric_data_quality(start, end):
    """data_quality dispatch helper: the lazy in-function import keeps
    queries import-cycle-free (quality only ever imports db)."""
    try:  # package-style entry (import agent.queries from the repo root)
        from agent import quality as _quality
    except ModuleNotFoundError:  # script-style (import queries from agent/)
        import quality as _quality
    return _quality.run_checks(start, end)


# Compiler dispatch (issue #77): every registry name maps onto an EXISTING
# implementation — kpis key lookups, churn_delta's current rate, the lazy
# quality statuses, and the two file-only metrics above that reuse the
# existing static templates. No new _SQL_* strings live behind this map.
_METRIC_FUNCS = {
    "net_new_arr": lambda s, e: kpis(s, e)["net_new_arr"],
    "mrr": lambda s, e: kpis(s, e)["mrr"],
    "nrr": lambda s, e: kpis(s, e)["nrr"],
    "active_accounts": lambda s, e: kpis(s, e)["accounts"],
    "churn_rate": lambda s, e: churn_delta(end=e)["current"],
    "data_quality": _metric_data_quality,
    "gross_churned_arr": lambda s, e: gross_churned_arr(s, e),
    "active_accounts_eom": lambda s, e: active_accounts_eom(e),
}


def metric_value(name, start, end=None):
    """Compiled registry value for metric `name` over [start, end or
    start]: the queries.py half of the issue #77 contract. Unknown names
    raise ValueError (quoted, like the _CHURN_METRICS whitelist) before
    any SQL runs."""
    if name not in _METRIC_FUNCS:
        raise ValueError(
            f"metric {name!r} is not whitelisted; "
            f"must be one of {tuple(_METRIC_FUNCS)}"
        )
    if end is None:
        end = start
    return _METRIC_FUNCS[name](start, end)

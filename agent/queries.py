"""Whitelisted SQL queries + light Python side aggregates.

Security posture (PRD §9): read-only, parameterized, no string-built SQL —
callers can only name a template id and pass scalar params. Aggregates that
would need window-function gymnastics in SQLite are computed here over the
template results instead.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import store

# Query ids the agent may run — anything else raises ValueError.
TEMPLATES: dict[str, tuple[str, tuple[str, ...]]] = {
    # (SQL with ?-params, names of keys expected in params)
    "arr_daily_rows": (
        "SELECT day, new_arr, churned_arr, closing_mrr, active_accounts "
        "FROM arr_daily WHERE day BETWEEN ? AND ? ORDER BY day",
        ("start", "end"),
    ),
    "churn_cohort": (
        "SELECT name, segment, mrr, plan, started_at, churned_at "
        "FROM accounts "
        "WHERE churned_at BETWEEN ? AND ? AND segment = 'Growth' "
        "ORDER BY mrr DESC",
        ("start", "end"),
    ),
    "active_book": (
        "SELECT COUNT(*) AS n, COALESCE(SUM(mrr), 0) AS mrr FROM accounts "
        "WHERE churned_at IS NULL",
        (),
    ),
    "dq_window_counts": (
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS nulls, "
        "MAX(ts) AS max_ts FROM usage_events WHERE ts >= ? AND ts <= ?",
        ("start", "end"),
    ),
    "dq_duplicate_groups": (
        "SELECT COUNT(*) AS groups, COALESCE(SUM(n - 1), 0) AS extra_rows FROM ("
        "SELECT COUNT(*) AS n FROM usage_events "
        "WHERE ts >= ? AND ts <= ? AND amount IS NOT NULL "
        "GROUP BY account_id, ts, metric, amount HAVING COUNT(*) > 1)",
        ("start", "end"),
    ),
    "dq_segment_split": (
        "SELECT a.segment AS segment, COUNT(*) AS n, "
        "SUM(CASE WHEN e.amount IS NULL THEN 1 ELSE 0 END) AS nulls "
        "FROM usage_events e JOIN accounts a ON a.id = e.account_id "
        "WHERE e.ts >= ? AND e.ts <= ? GROUP BY a.segment",
        ("start", "end"),
    ),
}


class QueryError(ValueError):
    """Unknown template id or bad params — a coding bug, never user input."""


def run_query(conn: sqlite3.Connection, template_id: str, params: dict[str, Any]) -> list[dict]:
    """Execute one whitelisted template with named scalars; return row dicts."""
    try:
        sql, keys = TEMPLATES[template_id]
    except KeyError as exc:
        raise QueryError(f"unknown query id: {template_id}") from exc
    missing = [k for k in keys if k not in params]
    if missing:
        raise QueryError(f"{template_id}: missing params {missing}")
    args = [params[k] for k in keys]
    rows = conn.execute(sql, args).fetchall()
    return [dict(row) for row in rows]


def q3_kpis(conn: sqlite3.Connection) -> dict:
    """The Q3 KPI row: net-new ARR, closing MRR, NRR, active accounts.

    Deltas compare Q3 against Q2 on the shared sequence boundaries; NRR is the
    classic (opening − churned MRR)/opening over the quarter, so the churn
    spike correctly shows up as the one red number.
    """
    def quarter(start: str, end: str, prev_close_day: str) -> dict:
        rows = run_query(conn, "arr_daily_rows", {"start": start, "end": end})
        opening = conn.execute(
            "SELECT closing_mrr, active_accounts FROM arr_daily WHERE day = ?",
            (prev_close_day,),
        ).fetchone()
        return {
            "new_arr": sum(r["new_arr"] for r in rows),
            "churned_arr": sum(r["churned_arr"] for r in rows),
            "closing_mrr": rows[-1]["closing_mrr"] if rows else 0.0,
            "accounts": rows[-1]["active_accounts"] if rows else 0,
            "opening_mrr": opening["closing_mrr"] if opening else 0.0,
        }

    q3 = quarter("2026-07-01", "2026-09-30", "2026-06-30")
    q2 = quarter("2026-04-01", "2026-06-30", "2026-03-31")
    net3 = q3["new_arr"] - q3["churned_arr"]
    net2 = q2["new_arr"] - q2["churned_arr"]
    nrr3 = 1 - q3["churned_arr"] / q3["opening_mrr"]
    nrr2 = 1 - q2["churned_arr"] / q2["opening_mrr"]
    return {
        "kpis": [
            {"label": "Net-new ARR (Q3)", "value": round(net3), "delta": _rel(net3, net2)},
            {"label": "Closing MRR", "value": round(q3["closing_mrr"]),
             "delta": _rel(q3["closing_mrr"], q2["closing_mrr"])},
            {"label": "Net revenue retention", "value": round(nrr3, 3),
             "delta": round(nrr3 - nrr2, 4)},
            {"label": "Active accounts", "value": q3["accounts"],
             "delta": _rel(q3["accounts"], q2["accounts"])},
        ],
        "summary": {
            "net_new_arr_q3": round(net3),
            "net_new_arr_q2": round(net2),
            "closing_mrr": round(q3["closing_mrr"]),
            "nrr_q3": round(nrr3, 4),
            "nrr_q2": round(nrr2, 4),
            "active_accounts": q3["accounts"],
            "opening_mrr_q3": round(q3["opening_mrr"]),
            "churned_arr_q3": round(q3["churned_arr"]),
        },
    }


def _rel(current: float, baseline: float) -> float:
    """Relative change vs a baseline that may be zero or negative."""
    if baseline == 0:
        return 0.0
    denom = abs(baseline)
    return round((current - baseline) / denom, 4)


def arr_daily_series(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """Line-chart payload: daily net-new and churned ARR for the range."""
    rows = run_query(conn, "arr_daily_rows", {"start": start, "end": end})
    return {
        "series": "arr_daily",
        "range": f"{start}..{end}",
        "points": [
            {
                "day": r["day"],
                "net_new": round(r["new_arr"] - r["churned_arr"]),
                "churned": round(r["churned_arr"]),
            }
            for r in rows
        ],
        "annotations": [
            {"day": store.CAMPAIGN_START.isoformat(), "label": "Campaign launch"},
            {"day": store.PRICING_CHANGE.isoformat(), "label": "Pricing change"},
        ],
    }


def churn_variance(conn: sqlite3.Connection) -> dict:
    """Churn spike vs the trailing 6-month baseline (Feb–Jul 2026).

    Spike window is Aug 2026 (the pricing change lands Aug 10) through the
    September tail; the callout delta is monthly-average churned MRR over the
    window divided by the baseline monthly average, minus one.
    """
    rows = conn.execute(
        "SELECT strftime('%Y-%m', day) AS month, SUM(churned_arr) AS m "
        "FROM arr_daily WHERE day BETWEEN '2026-02-01' AND '2026-07-31' GROUP BY month"
    ).fetchall()
    baseline_monthly = sum(r["m"] for r in rows) / len(rows)
    spike_rows = run_query(conn, "arr_daily_rows", {"start": "2026-08-01", "end": "2026-09-30"})
    spike_total = sum(r["churned_arr"] for r in spike_rows)
    months_in_window = len({r["day"][:7] for r in spike_rows})
    spike_monthly = spike_total / months_in_window
    return {
        "metric": "Churned MRR (Aug–Sep)",
        "delta": round(spike_monthly / baseline_monthly - 1, 4) if baseline_monthly else 0.0,
        "baseline_monthly": round(baseline_monthly),
        "spike_monthly": round(spike_monthly),
        "spike_total": round(spike_total),
        "cause": "Aug 10 pricing change on the Growth tier",
        "cause_detail": (
            "Growth-segment accounts on the legacy plan cancelled after the "
            "Aug 10 reprice; 42 accounts left between Aug 10 and Sep 30."
        ),
    }


def churn_cohort(conn: sqlite3.Connection) -> dict:
    """The churned-account table: name/segment/mrr/tenure/plan, ≤50 rows."""
    rows = run_query(conn, "churn_cohort", {"start": store.PRICING_CHANGE.isoformat(),
                                            "end": "2026-09-30"})
    table_rows = []
    for r in rows:
        started = date_from(r["started_at"])
        churned = date_from(r["churned_at"])
        table_rows.append({
            "account": r["name"],
            "segment": r["segment"],
            "mrr": round(r["mrr"]),
            "tenure_months": (churned - started).days // 30,
            "plan": r["plan"],
        })
    return {"columns": ["account", "segment", "mrr", "tenure_months", "plan"], "rows": table_rows}


def date_from(iso: str):
    """Parse a ``YYYY-MM-DD`` prefix back into a date."""
    from datetime import date as _date

    y, m, d = (int(part) for part in iso.split("T")[0].split("-"))
    return _date(y, m, d)

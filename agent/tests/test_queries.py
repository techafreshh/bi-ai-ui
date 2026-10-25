"""Query-layer tests: the Q3/KPI, churn and series aggregates match the demo story."""

from __future__ import annotations

import queries
import store


def conn():
    return store.connect()


def test_q3_kpis_hit_the_golden_story():
    kpis = queries.q3_kpis(conn())
    tiles = {k["label"]: k for k in kpis["kpis"]}
    # PRD §5 story 1: net-new ARR, MRR, NRR, accounts — ≥3 positive deltas.
    positive = sum(1 for k in tiles.values() if isinstance(k["delta"], (int, float)) and k["delta"] > 0)
    assert positive >= 3
    assert tiles["Net-new ARR (Q3)"]["delta"] > 0
    assert tiles["Closing MRR"]["delta"] > 0
    assert tiles["Active accounts"]["delta"] > 0
    # The churn spike honestly dents NRR — that is the pytest-visible story.
    nrr = tiles["Net revenue retention"]
    assert 0 < nrr["value"] < 1
    assert nrr["delta"] < 0
    assert kpis["summary"]["net_new_arr_q3"] > 0


def test_q3_kpis_compose_from_correct_windows():
    kpis = queries.q3_kpis(conn())
    summary = kpis["summary"]
    assert summary["net_new_arr_q3"] > summary["net_new_arr_q2"]
    assert summary["churned_arr_q3"] > 0


def test_churn_cohort_is_exactly_42_growth_accounts():
    cohort = queries.churn_cohort(conn())
    assert cohort["columns"] == ["account", "segment", "mrr", "tenure_months", "plan"]
    rows = cohort["rows"]
    # Probe gate accepts 38–50; the seed pins exactly 42.
    assert 38 <= len(rows) <= 50
    assert len(rows) == store.CHURN_ACCOUNTS
    assert all(r["segment"] == "Growth" for r in rows)
    assert all(r["mrr"] > 0 for r in rows)
    assert all(r["tenure_months"] >= 14 for r in rows)
    assert all(r["plan"] == "Growth Legacy" for r in rows)
    # Sorted by MRR descending (largest loss first in the table).
    mrrs = [r["mrr"] for r in rows]
    assert mrrs == sorted(mrrs, reverse=True)


def test_churn_variance_exceeds_probe_gate():
    var = queries.churn_variance(conn())
    assert var["delta"] > 0.2
    assert var["baseline_monthly"] > 0
    assert var["spike_monthly"] > var["baseline_monthly"]
    assert "pricing change" in var["cause"].lower()


def test_arr_daily_series_covers_q3():
    series = queries.arr_daily_series(conn(), "2026-07-01", "2026-09-30")
    assert series["range"] == "2026-07-01..2026-09-30"
    assert len(series["points"]) == 92  # Jul 31 + Aug 31 + Sep 30
    assert all("net_new" in p and "churned" in p for p in series["points"])
    days = [p["day"] for p in series["points"]]
    assert days[0] == "2026-07-01" and days[-1] == "2026-09-30"
    # Campaign and pricing-change annotations are part of the chart contract.
    labels = {a["label"] for a in series["annotations"]}
    assert "Campaign launch" in labels and "Pricing change" in labels


def test_unknown_template_rejected():
    import pytest

    from queries import QueryError

    with pytest.raises(QueryError):
        queries.run_query(conn(), "DROP TABLE accounts; --", {})
    with pytest.raises(QueryError):
        queries.run_query(conn(), "churn_cohort", {})

"""Acceptance-contract tests for the SQL query layer (issue #5).

One test per acceptance clause of issue #5 (plus the validation and
import-behavior behavior the issue and plan pin), each with a docstring
naming the clause it proves. The generator drives a fresh temp DB through
its real entrypoint as a subprocess (mirroring the runtime gate) per the
session-fixture idiom of test_generate_data.py; queries.py opens only
db.connect(readonly=True) against db.DEFAULT_DB_PATH, so the single
supported seam the suite needs is that module default.
"""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

import db
import queries

AGENT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = AGENT_DIR / "scripts" / "generate_data.py"
RUN_TIMEOUT_SECONDS = 600

INJECTION = "2026-07-01'; DROP TABLE accounts;--"


def _run_generator(db_path: Path) -> None:
    """Run the generator like the runtime gate does, failing on nonzero exit."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--db-path", str(db_path)],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    """One seeded temp DB for the session; while it is in place, the query
    layer's module default points at it so every public function reads the
    seeded data (the default is restored afterwards)."""
    db_path = tmp_path_factory.mktemp("queries") / "seeded.db"
    _run_generator(db_path)
    original = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = db_path
    try:
        yield db_path
    finally:
        db.DEFAULT_DB_PATH = original


def _is_number(value) -> bool:
    """JSON-numeric in the strict sense needed here (bool is an int subclass
    in Python but is not a KPI number)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def test_kpis_q3_contract(seeded_db):
    """Acceptance: kpis(2026-07-01..2026-09-30) returns exactly the keys
    net_new_arr / mrr / nrr / accounts, each numeric, and the monthly
    net-new ordering Aug > Jul > 0 holds."""
    kpis = queries.kpis("2026-07-01", "2026-09-30")
    assert set(kpis) == {"net_new_arr", "mrr", "nrr", "accounts"}
    for key, value in kpis.items():
        assert _is_number(value), key
    july = queries.kpis("2026-07-01", "2026-07-31")["net_new_arr"]
    august = queries.kpis("2026-08-01", "2026-08-31")["net_new_arr"]
    assert august > july > 0


def test_net_new_arr_matches_cents_exactly(seeded_db):
    """Unit pin: net_new_arr is dollars — per month it equals the raw
    SUM(arr_delta_cents) over the window converted at exactly /100 (the
    generator's cent-integer money is a whole number of dollars here)."""
    conn = db.connect(seeded_db, readonly=True)
    try:
        for start, end in (
            ("2026-07-01", "2026-07-31"),
            ("2026-08-01", "2026-08-31"),
            ("2026-09-01", "2026-09-30"),
        ):
            raw_cents = conn.execute(
                "SELECT COALESCE(SUM(arr_delta_cents), 0) FROM subscription_events "
                "WHERE event_date BETWEEN ? AND ?",
                (start, end),
            ).fetchone()[0]
            kpis = queries.kpis(start, end)
            assert round(kpis["net_new_arr"] * 100) == raw_cents, (start, end)
    finally:
        conn.close()


def test_mrr_is_point_in_time(seeded_db):
    """mrr(end) sums only events <= end: the July KPI is strictly below the
    naive final-ARR basis (SUM of accounts.arr_cents for accounts created by
    Jul 31 not churned by Jul 31, / 12), because Aug-Sep upgrades — the
    churn cohort's repricings included — must not leak into July."""
    conn = db.connect(seeded_db, readonly=True)
    try:
        naive_final_cents = conn.execute(
            "SELECT COALESCE(SUM(a.arr_cents), 0) FROM accounts a "
            "WHERE a.created_at <= '2026-07-31' "
            "  AND NOT EXISTS (SELECT 1 FROM subscription_events s "
            "                  WHERE s.account_id = a.id AND s.event_type = 'cancel' "
            "                    AND s.event_date <= '2026-07-31')"
        ).fetchone()[0]
    finally:
        conn.close()
    july_mrr = queries.kpis("2026-07-01", "2026-07-31")["mrr"]
    assert july_mrr * 1200 < naive_final_cents


def test_arr_series_daily_contract(seeded_db):
    """Acceptance shape for arr_series: Q3 yields exactly 92 rows with
    consecutive ISO dates and numeric arr >= 0; the series grows over the
    quarter (last >= first); and the September series' last day agrees with
    the September kpis point-in-time ARR (mrr * 12) within the rounding
    envelope (each side rounds the same ARR cents independently, so they may
    differ by at most 12 * 0.005 + 0.005 dollars)."""
    rows = queries.arr_series("2026-07-01", "2026-09-30")
    assert len(rows) == 92
    first_day = date(2026, 7, 1)
    for i, row in enumerate(rows):
        assert set(row) == {"date", "arr"}
        assert row["date"] == (first_day + timedelta(days=i)).isoformat()
        assert _is_number(row["arr"]) and row["arr"] >= 0
    assert rows[-1]["arr"] >= rows[0]["arr"]

    september = queries.arr_series("2026-09-01", "2026-09-30")
    assert len(september) == 30
    sept_mrr = queries.kpis("2026-09-01", "2026-09-30")["mrr"]
    assert abs(september[-1]["arr"] - sept_mrr * 12) <= 0.13


def test_churn_cohort_contract(seeded_db):
    """Acceptance: churn_cohort(2026-08-10..2026-09-30) returns <= 50 rows,
    each with exactly segment / mrr / tenure_months / plan, >= 80% segment
    'Growth', positive mrr and tenure; the row count equals the raw cancel
    count; and the limit cap works with stable ordering."""
    cohort = queries.churn_cohort(("2026-08-10", "2026-09-30"))
    assert len(cohort) <= 50
    for row in cohort:
        assert set(row) == {"segment", "mrr", "tenure_months", "plan"}
        assert row["mrr"] > 0
        assert row["tenure_months"] > 0
    growth_share = sum(row["segment"] == "Growth" for row in cohort) / len(cohort)
    assert growth_share >= 0.8

    conn = db.connect(seeded_db, readonly=True)
    try:
        raw_cancel_count = conn.execute(
            "SELECT COUNT(*) FROM subscription_events "
            "WHERE event_type = 'cancel' AND event_date BETWEEN '2026-08-10' AND '2026-09-30'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert len(cohort) == raw_cancel_count

    full = queries.churn_cohort(("2024-10-01", "2026-09-30"), limit=50)
    capped = queries.churn_cohort(("2024-10-01", "2026-09-30"), limit=5)
    assert capped == full[:5] and len(capped) == 5


def test_churn_delta_contract(seeded_db):
    """Acceptance: churn_delta(churn_rate, end=2026-09-30, baseline_months=6)
    returns exactly delta / baseline / current with delta > 0.2, the current
    month-to-date rate above the trailing baseline, and both real rates in
    (0, 1)."""
    result = queries.churn_delta(
        metric="churn_rate", end="2026-09-30", baseline_months=6
    )
    assert set(result) == {"delta", "baseline", "current"}
    assert result["delta"] > 0.2
    assert result["current"] > result["baseline"] > 0
    assert 0 < result["baseline"] < 1
    assert 0 < result["current"] < 1


def test_latest_event_date_contract(seeded_db):
    """Latest observed event day across all three event tables = the seed's
    horizon 2026-09-30 — the deterministic data_end anchor read_state
    resolves query windows against."""
    assert queries.latest_event_date() == "2026-09-30"


def test_latest_event_date_returns_none_on_eventless_db(tmp_path, monkeypatch):
    """A DB with the schema but zero events reads as None (the fallback to
    the wall-clock anchor is read_state's decision, not the query layer's)."""
    from quality_fixture import build_fixture

    db_path = tmp_path / "eventless.db"
    build_fixture(db_path, usage_rows=[])
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    assert queries.latest_event_date() is None


def test_sql_injection_safety(seeded_db):
    """Acceptance: attempted injection values passed as parameters raise
    ValueError (never reach SQL), and the accounts table still exists with
    its row count unchanged afterwards."""
    conn = db.connect(seeded_db, readonly=True)
    try:
        accounts_before = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    finally:
        conn.close()

    with pytest.raises(ValueError):
        queries.kpis(INJECTION, "2026-09-30")
    with pytest.raises(ValueError):
        queries.kpis("2026-07-01", INJECTION)
    with pytest.raises(ValueError):
        queries.arr_series(INJECTION, "2026-09-30")
    with pytest.raises(ValueError):
        queries.churn_cohort((INJECTION, "2026-09-30"))
    with pytest.raises(ValueError):
        queries.churn_cohort(("2026-08-10", INJECTION))
    with pytest.raises(ValueError):
        queries.churn_delta(end=INJECTION)
    with pytest.raises(ValueError):
        queries.churn_delta(end="2026-09-30", metric=f"churn_rate{INJECTION}")

    conn = db.connect(seeded_db, readonly=True)
    try:
        accounts_after = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    finally:
        conn.close()
    assert accounts_after == accounts_before


def test_input_validation(seeded_db):
    """Contract edges: reversed windows, non-tuple and wrong-length churn
    windows, an unknown churn metric and baseline_months = 0 are all
    ValueError before any SQL runs; the cohort limit must be >= 1."""
    with pytest.raises(ValueError):
        queries.kpis("2026-09-30", "2026-07-01")
    with pytest.raises(ValueError):
        queries.arr_series("2026-09-30", "2026-07-01")
    with pytest.raises(ValueError):
        queries.churn_cohort("2026-08-10")
    with pytest.raises(ValueError):
        queries.churn_cohort(("2026-08-10",))
    with pytest.raises(ValueError):
        queries.churn_delta(end="2026-09-30", metric="exec")
    with pytest.raises(ValueError):
        queries.churn_delta(end="2026-09-30", baseline_months=0)
    with pytest.raises(ValueError):
        queries.churn_cohort(("2026-08-10", "2026-09-30"), limit=0)


def test_module_imports_both_styles():
    """Entry contract: queries works both as `import agent.queries` from the
    repo root (package-style runtime probes) and as `import queries` from
    agent/ (tests), via the same dual import as server.py."""
    package_style = subprocess.run(
        [sys.executable, "-c", "import agent.queries"],
        cwd=AGENT_DIR.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert package_style.returncode == 0, package_style.stderr
    script_style = subprocess.run(
        [sys.executable, "-c", "import queries"],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert script_style.returncode == 0, script_style.stderr

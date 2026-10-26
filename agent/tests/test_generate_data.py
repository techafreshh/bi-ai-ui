"""Acceptance-contract tests for the seeded data generator (issues #3+#4).

Each test maps to one acceptance clause of issue #3 or #4, or to an
invariant the generator's docstring promises. The generator is driven
through its real entrypoint (`scripts/generate_data.py`) as a subprocess —
mirroring the runtime gate, which invokes the script itself — with
`--db-path` redirecting output into temp dirs, per the subprocess idiom of
test_server.py.
"""

import sqlite3
import statistics
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import db

AGENT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = AGENT_DIR / "scripts" / "generate_data.py"
# One generation takes ~10s; the session runs three of them.
RUN_TIMEOUT_SECONDS = 600

TABLES = ("accounts", "subscription_events", "revenue_events", "usage_events")
EVENT_TABLES = ("subscription_events", "revenue_events", "usage_events")
EXPECTED_COLUMNS = {
    "accounts": ("id", "name", "segment", "plan", "arr_cents", "created_at"),
    "subscription_events": ("id", "account_id", "event_date", "event_type", "arr_delta_cents"),
    "revenue_events": ("id", "account_id", "event_date", "amount_cents"),
    "usage_events": ("id", "account_id", "event_date", "metric", "units"),
}


def _run_generator(db_path: Path) -> None:
    """Run the script like the runtime gate does, failing on nonzero exit."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--db-path", str(db_path)],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _snapshot(db_path: Path) -> dict:
    """Per-table row counts, total revenue in cents (the issue-#3 cent-exact
    determinism currency) and the issue-#4 planted-shape metrics (churn
    cancel rows in the window, their Growth-segment count, and the
    Startup-segment duplicate / null-amount usage rows inside the bug
    window) — the numbers the rerun-identically acceptance clause pins."""
    conn = db.connect(db_path, readonly=True)
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM revenue_events"
        ).fetchone()[0]
        churn_cancels = conn.execute(
            "SELECT COUNT(*) FROM subscription_events "
            "WHERE event_type = 'cancel' "
            "AND event_date BETWEEN '2026-08-10' AND '2026-09-30'"
        ).fetchone()[0]
        churn_growth_count = conn.execute(
            "SELECT COUNT(*) FROM subscription_events s "
            "JOIN accounts a ON a.id = s.account_id "
            "WHERE s.event_type = 'cancel' "
            "AND s.event_date BETWEEN '2026-08-10' AND '2026-09-30' "
            "AND a.segment = 'Growth'"
        ).fetchone()[0]
        startup_dup_rows = conn.execute(
            "SELECT COALESCE(SUM(n - 1), 0) FROM ("
            "  SELECT COUNT(*) AS n FROM usage_events u "
            "  JOIN accounts a ON a.id = u.account_id "
            "  WHERE u.event_date BETWEEN '2026-09-08' AND '2026-09-21' "
            "  AND a.segment = 'Startup' "
            "  GROUP BY u.account_id, u.event_date, u.units HAVING n > 1"
            ")"
        ).fetchone()[0]
        startup_null_rows = conn.execute(
            "SELECT COUNT(*) FROM usage_events u "
            "JOIN accounts a ON a.id = u.account_id "
            "WHERE u.event_date BETWEEN '2026-09-08' AND '2026-09-21' "
            "AND a.segment = 'Startup' AND u.units IS NULL"
        ).fetchone()[0]
        return {
            "counts": counts,
            "total_revenue_cents": total_revenue,
            "churn_cancels": churn_cancels,
            "churn_growth_count": churn_growth_count,
            "startup_dup_rows": startup_dup_rows,
            "startup_null_rows": startup_null_rows,
        }
    finally:
        conn.close()


@pytest.fixture(scope="session")
def generated(tmp_path_factory):
    """Generate DB A, DB B, then regenerate over A (rerun over the same
    path must be idempotent) and snapshot each state."""
    tmp = tmp_path_factory.mktemp("generate_data")
    db_a, db_b = tmp / "a.db", tmp / "b.db"
    _run_generator(db_a)
    first = _snapshot(db_a)
    _run_generator(db_b)
    second = _snapshot(db_b)
    _run_generator(db_a)
    rerun = _snapshot(db_a)
    return db_a, first, second, rerun


def test_deterministic_row_counts_and_total_revenue(generated):
    """Acceptance: two runs produce identical per-table row counts and
    identical total revenue to the cent; a rerun over the same path too."""
    _db_a, first, second, rerun = generated
    assert first["counts"] == second["counts"] == rerun["counts"]
    assert (
        first["total_revenue_cents"]
        == second["total_revenue_cents"]
        == rerun["total_revenue_cents"]
    )


def test_account_row_count_in_contract_range(generated):
    """Acceptance: the accounts table holds between 5000 and 8000 rows."""
    _db_a, first, _second, _rerun = generated
    assert 5000 <= first["counts"]["accounts"] <= 8000


def test_q3_2026_net_new_arr_growth_shape(generated):
    """Acceptance: Jul/Aug/Sep 2026 net-new ARR — each month ≥ the prior
    month and Aug+Sep ≥ 1.5× Jul (campaign shape), plus the design-headroom
    check Aug ≥ 1.5× Jul that the pinned 2×/3× cohort counts guarantee."""
    db_a, *_ = generated
    conn = db.connect(db_a, readonly=True)
    try:
        rows = conn.execute(
            "SELECT substr(event_date, 1, 7) AS ym, SUM(arr_delta_cents) "
            "FROM subscription_events "
            "WHERE event_date BETWEEN '2026-07-01' AND '2026-09-30' GROUP BY ym"
        ).fetchall()
    finally:
        conn.close()
    monthly = {row[0]: row[1] for row in rows}
    july = monthly["2026-07"]
    august = monthly["2026-08"]
    september = monthly["2026-09"]
    assert july > 0
    assert august >= july
    assert september >= august
    assert august + september >= 1.5 * july
    assert august >= 1.5 * july


def test_event_date_bounds(generated):
    """Acceptance: min event date within 2024-10-01..2024-10-31 (pinned by
    the founding cohort) and max exactly 2026-09-30, for every event table
    (ISO dates compare correctly as strings)."""
    db_a, *_ = generated
    conn = db.connect(db_a, readonly=True)
    try:
        for table in EVENT_TABLES:
            min_date, max_date = conn.execute(
                f"SELECT MIN(event_date), MAX(event_date) FROM {table}"
            ).fetchone()
            assert "2024-10-01" <= min_date <= "2024-10-31", table
            assert max_date == "2026-09-30", table
    finally:
        conn.close()


def test_documented_schema(generated):
    """The four documented tables exist with their documented columns, and
    every foreign key resolves."""
    db_a, *_ = generated
    conn = db.connect(db_a, readonly=True)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, columns in EXPECTED_COLUMNS.items():
            assert table in existing, table
            actual = tuple(
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            )
            assert actual == columns, table
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_account_arr_equals_subscription_deltas(generated):
    """Docstring invariant: accounts.arr_cents equals the per-account sum of
    its subscription events' arr_delta_cents (final plan/ARR bookkeeping)."""
    db_a, *_ = generated
    conn = db.connect(db_a, readonly=True)
    try:
        mismatches = conn.execute(
            "SELECT COUNT(*) FROM accounts a LEFT JOIN "
            "(SELECT account_id, SUM(arr_delta_cents) AS total "
            " FROM subscription_events GROUP BY account_id) s "
            "ON s.account_id = a.id WHERE COALESCE(s.total, 0) <> a.arr_cents"
        ).fetchone()[0]
        assert mismatches == 0
    finally:
        conn.close()


def test_churn_cohort_contract(generated):
    """Acceptance (issue #4, churn shape): cancel events in
    2026-08-10..2026-09-30 number 38–45, ≥80% of the churned accounts are
    Growth-segment, the median tenure exceeds six months (183 days), and
    every churned account carries the paired in-window "new plan" upgrade
    dated on or before its zero-delta cancel marker."""
    db_a, *_ = generated
    conn = db.connect(db_a, readonly=True)
    try:
        cancels = conn.execute(
            "SELECT s.account_id, s.event_date AS cancel_date, "
            "       s.arr_delta_cents, a.segment, a.created_at, "
            "       (SELECT COUNT(*) FROM subscription_events se "
            "         WHERE se.account_id = s.account_id "
            "           AND se.event_type = 'upgrade' "
            "           AND se.event_date BETWEEN '2026-08-10' AND s.event_date) "
            "           AS paired_upgrades "
            "FROM subscription_events s "
            "JOIN accounts a ON a.id = s.account_id "
            "WHERE s.event_type = 'cancel' "
            "  AND s.event_date BETWEEN '2026-08-10' AND '2026-09-30'"
        ).fetchall()
    finally:
        conn.close()
    assert 38 <= len(cancels) <= 45
    growth = sum(row["segment"] == "Growth" for row in cancels)
    assert growth >= 0.8 * len(cancels)
    tenures = [
        (date.fromisoformat(row["cancel_date"])
         - date.fromisoformat(row["created_at"])).days
        for row in cancels
    ]
    assert statistics.median(tenures) > 183
    for row in cancels:
        assert row["arr_delta_cents"] == 0  # 'cancel' is the churn marker
        assert row["paired_upgrades"] >= 1  # the "new plan" precedes the churn


def test_usage_ingest_bug_contract(generated):
    """Acceptance (issue #4, ingest bug): inside the 2026-09-08..21 window
    Startup rows are ≥5% exact duplicates (every duplicated group contains
    exactly 2 rows) with >2% NULL amounts, while every other segment stays
    below 0.5% on both metrics — the bug touches Startup rows only."""
    db_a, *_ = generated
    conn = db.connect(db_a, readonly=True)
    try:
        groups = conn.execute(
            "SELECT a.segment AS segment, u.account_id, u.event_date, "
            "       u.units, COUNT(*) AS n "
            "FROM usage_events u JOIN accounts a ON a.id = u.account_id "
            "WHERE u.event_date BETWEEN '2026-09-08' AND '2026-09-21' "
            "GROUP BY a.segment, u.account_id, u.event_date, u.units"
        ).fetchall()
        distinct_metrics = conn.execute(
            "SELECT COUNT(DISTINCT metric) FROM usage_events "
            "WHERE event_date BETWEEN '2026-09-08' AND '2026-09-21'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert distinct_metrics == 1  # 'amount' is units; metric is constant
    rows_in = {}
    dup_rows = {}
    null_rows = {}
    max_group = {"Startup": 0}
    for row in groups:
        segment = row["segment"]
        rows_in[segment] = rows_in.get(segment, 0) + row["n"]
        if row["units"] is None:
            null_rows[segment] = null_rows.get(segment, 0) + row["n"]
        if row["n"] > 1:
            dup_rows[segment] = dup_rows.get(segment, 0) + row["n"] - 1
            max_group[segment] = max(max_group.get(segment, 0), row["n"])
    startup_rows = rows_in["Startup"]
    assert dup_rows["Startup"] / startup_rows >= 0.05
    assert max_group["Startup"] == 2  # an exact duplicate appears twice
    assert null_rows["Startup"] / startup_rows > 0.02
    for segment in sorted(rows_in):
        if segment == "Startup":
            continue
        assert dup_rows.get(segment, 0) < 0.005 * rows_in[segment]
        assert null_rows.get(segment, 0) < 0.005 * rows_in[segment]


def test_planted_shapes_rerun_identical(generated):
    """Acceptance: rerunning the generator reproduces all three planted
    shapes with identical counts — the Q3-2026 growth ramp is pinned by the
    per-table counts plus test_q3_2026_net_new_arr_growth_shape; the churn
    cohort and the ingest bug must match on a fresh second run and on a
    rerun over the same path."""
    _db_a, first, second, rerun = generated
    for key in (
        "churn_cancels", "churn_growth_count", "startup_dup_rows", "startup_null_rows",
    ):
        assert first[key] == second[key] == rerun[key], key
    # The shapes are present at all (a vacuous 0/0 equality proves nothing).
    assert 38 <= first["churn_cancels"] <= 45
    assert first["churn_growth_count"] == first["churn_cancels"]
    assert first["startup_dup_rows"] > 0
    assert first["startup_null_rows"] > 0


def test_db_helper_contract(tmp_path):
    """`db.connect()`: Row rows, foreign_keys on, readonly refuses writes,
    DEFAULT_DB_PATH is the repo-root data/app.db regardless of cwd."""
    assert db.DEFAULT_DB_PATH == AGENT_DIR.parent / "data" / "app.db"

    rw = db.connect(tmp_path / "helper.db")
    assert rw.row_factory is sqlite3.Row
    assert rw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    rw.execute("CREATE TABLE t (x INTEGER)")
    rw.commit()
    rw.close()

    ro = db.connect(tmp_path / "helper.db", readonly=True)
    assert ro.execute("SELECT 1 AS one").fetchone()["one"] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO t VALUES (1)")
    ro.close()

    # Readonly mode must fail on a missing file instead of creating one.
    with pytest.raises(sqlite3.OperationalError):
        db.connect(tmp_path / "missing.db", readonly=True)

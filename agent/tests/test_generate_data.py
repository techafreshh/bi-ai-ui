"""Issue #3 acceptance-contract tests for the seeded data generator.

Each test maps to one acceptance clause of issue #3 or to an invariant the
generator's docstring promises. The generator is driven through its real
entrypoint (`scripts/generate_data.py`) as a subprocess — mirroring the
runtime gate, which invokes the script itself — with `--db-path` redirecting
output into temp dirs, per the subprocess idiom of test_server.py.
"""

import sqlite3
import subprocess
import sys
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
    """Per-table row counts plus total revenue in cents (the cent-exact
    determinism currency the issue pins)."""
    conn = db.connect(db_path, readonly=True)
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM revenue_events"
        ).fetchone()[0]
        return {"counts": counts, "total_revenue_cents": total_revenue}
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

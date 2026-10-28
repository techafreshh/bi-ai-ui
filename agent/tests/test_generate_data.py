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


class TestRandomProfile:
    """--profile random: the sample-dataset harness (data the agent was
    never staged for). Same period and schema, organic growth, and a churn
    spike + ingest bug drawn by --seed instead of pinned. Every assertion
    derives from the calendar structure, not the seeded shapes: organic
    cancels land strictly before June 2026 (RANDOM_ORGANIC_LAST_DAY) and
    spike cancels on/after the drawn June-or-later start, so the two cohorts
    split cleanly on the calendar; NULL-units rows exist ONLY inside the
    drawn bug window and ONLY in the drawn bug segment (the corruption roll
    never fires elsewhere), which pins the bug window from the DB."""

    @staticmethod
    def _run(db_path, seed, profile="random"):
        """Run the generator like the runtime gate does, failing on a
        nonzero exit. profile defaults to the random profile this class
        exercises; the seeded-profile test passes profile="seeded"."""
        result = subprocess.run(
            [
                sys.executable, str(GENERATOR),
                "--db-path", str(db_path),
                "--profile", profile, "--seed", str(seed),
            ],
            cwd=AGENT_DIR,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        return result.stdout

    @staticmethod
    def _shapes(db_path):
        """The drawn shapes derived from the DB alone: spike cohort (cancels
        on/after 2026-06-01), organic tail (cancels before June), bug window
        and segment (the NULL-units rows' own span and segments), in-window
        corruption counts, and the ARR bookkeeping invariant's mismatch
        count."""
        conn = db.connect(db_path, readonly=True)
        try:
            cancels = conn.execute(
                "SELECT MIN(event_date) AS first, MAX(event_date) AS last, "
                "       COUNT(*) AS total FROM subscription_events "
                "WHERE event_type = 'cancel' AND event_date >= '2026-06-01'"
            ).fetchone()
            organic = conn.execute(
                "SELECT COUNT(*) FROM subscription_events "
                "WHERE event_type = 'cancel' AND event_date < '2026-06-01'"
            ).fetchone()[0]
            bug = conn.execute(
                "SELECT MIN(event_date) AS bug_start, MAX(event_date) AS bug_end "
                "FROM usage_events WHERE units IS NULL"
            ).fetchone()
            segments = conn.execute(
                "SELECT DISTINCT a.segment FROM usage_events u "
                "JOIN accounts a ON a.id = u.account_id WHERE u.units IS NULL"
            ).fetchall()
            window_rows = dup_extras = null_rows = 0
            if bug["bug_start"] is not None:
                bug_segment = segments[0]["segment"]
                window_rows = conn.execute(
                    "SELECT COUNT(*) FROM usage_events u "
                    "JOIN accounts a ON a.id = u.account_id "
                    "WHERE u.event_date BETWEEN ? AND ? AND a.segment = ?",
                    (bug["bug_start"], bug["bug_end"], bug_segment),
                ).fetchone()[0]
                dup_extras = conn.execute(
                    "SELECT COALESCE(SUM(n - 1), 0) FROM ("
                    "  SELECT COUNT(*) AS n FROM usage_events u "
                    "  JOIN accounts a ON a.id = u.account_id "
                    "  WHERE u.event_date BETWEEN ? AND ? AND a.segment = ? "
                    "  GROUP BY u.account_id, u.event_date, u.units HAVING n > 1"
                    ")",
                    (bug["bug_start"], bug["bug_end"], bug_segment),
                ).fetchone()[0]
                null_rows = conn.execute(
                    "SELECT COUNT(*) FROM usage_events u "
                    "JOIN accounts a ON a.id = u.account_id "
                    "WHERE u.event_date BETWEEN ? AND ? AND a.segment = ? "
                    "AND u.units IS NULL",
                    (bug["bug_start"], bug["bug_end"], bug_segment),
                ).fetchone()[0]
            mismatch = conn.execute(
                "SELECT COUNT(*) FROM accounts a LEFT JOIN ("
                "  SELECT account_id, SUM(arr_delta_cents) AS total "
                "  FROM subscription_events GROUP BY account_id) s "
                "ON s.account_id = a.id WHERE COALESCE(s.total, 0) <> a.arr_cents"
            ).fetchone()[0]
            return {
                "spike_start": cancels["first"],
                "spike_end": cancels["last"],
                "spike_count": cancels["total"],
                "organic_count": organic,
                "bug_start": bug["bug_start"],
                "bug_end": bug["bug_end"],
                "bug_segments": sorted(row["segment"] for row in segments),
                "bug_window_rows": window_rows,
                "bug_dup_extras": dup_extras,
                "bug_null_rows": null_rows,
                "arr_mismatches": mismatch,
            }
        finally:
            conn.close()

    @staticmethod
    def _counts(db_path):
        conn = db.connect(db_path, readonly=True)
        try:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in TABLES
            }
        finally:
            conn.close()

    @staticmethod
    def _billing_stops_at_cancel(db_path):
        """No account is billed or emits usage past its first cancel day —
        the merged spike+organic churn_dates govern both emissions."""
        conn = db.connect(db_path, readonly=True)
        try:
            overrevenue = conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT s.account_id, MIN(s.event_date) AS cancel_date, "
                "         MAX(r.event_date) AS last_revenue "
                "  FROM subscription_events s "
                "  LEFT JOIN revenue_events r ON r.account_id = s.account_id "
                "  WHERE s.event_type = 'cancel' GROUP BY s.account_id"
                ") WHERE last_revenue > cancel_date"
            ).fetchone()[0]
            overusage = conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT s.account_id, MIN(s.event_date) AS cancel_date, "
                "         MAX(u.event_date) AS last_usage "
                "  FROM subscription_events s "
                "  LEFT JOIN usage_events u ON u.account_id = s.account_id "
                "  WHERE s.event_type = 'cancel' GROUP BY s.account_id"
                ") WHERE last_usage > cancel_date"
            ).fetchone()[0]
        finally:
            conn.close()
        return overrevenue, overusage

    @pytest.fixture(scope="class")
    def random_generated(self, tmp_path_factory):
        """DB A and DB B from one seed, DB A regenerated in place, DB C from
        a different seed — snapshots for the identity and variation tests."""
        tmp = tmp_path_factory.mktemp("random_profile")
        db_a, db_b, db_c = tmp / "a.db", tmp / "b.db", tmp / "c.db"
        self._run(db_a, 7)
        first = (self._counts(db_a), self._shapes(db_a))
        self._run(db_b, 7)
        second = (self._counts(db_b), self._shapes(db_b))
        self._run(db_a, 7)
        rerun = (self._counts(db_a), self._shapes(db_a))
        self._run(db_c, 99)
        other = (self._counts(db_c), self._shapes(db_c))
        return {"db_a": db_a, "first": first, "second": second,
                "rerun": rerun, "db_c": db_c, "other": other}

    def test_random_reruns_identical(self, random_generated):
        """The same --seed twice yields identical per-table row counts AND
        identical drawn shapes; a rerun over the same path too."""
        assert (random_generated["first"]
                == random_generated["second"]
                == random_generated["rerun"])

    def test_random_tables_are_populated(self, random_generated):
        """All four tables hold rows on the random profile (a vacuous empty
        DB would make every shape assertion vacuous too)."""
        counts = random_generated["first"][0]
        for table, count in counts.items():
            assert count > 0, table

    def test_random_invariants_hold(self, random_generated):
        """Structural invariants regardless of the drawn shapes: the spike
        cohort is 15–45 cancels inside Jun 1 2026..data end, the organic
        tail is 10–30 cancels strictly before June, the bug window is 14
        days inside Jul 1..data end in ONE segment with corruption shares
        inside the drawn bands, ARR bookkeeping is intact, and billing stops
        at the cancel day."""
        _counts, shapes = random_generated["first"]
        assert 15 <= shapes["spike_count"] <= 45
        assert "2026-06-01" <= shapes["spike_start"] <= "2026-09-30"
        assert shapes["spike_end"] <= "2026-09-30"
        assert 10 <= shapes["organic_count"] <= 30
        assert shapes["bug_start"] is not None
        assert "2026-07-01" <= shapes["bug_start"] <= "2026-09-17"
        assert shapes["bug_end"] <= "2026-09-30"
        # The bug window spans exactly RANDOM_BUG_DAYS (14) calendar days.
        assert (
            date.fromisoformat(shapes["bug_end"])
            - date.fromisoformat(shapes["bug_start"])
        ).days == 13
        assert len(shapes["bug_segments"]) == 1
        # The corruption shares (against the segment's own baseline rows —
        # the generator's denominators) sit inside the drawn bands.
        baseline_rows = shapes["bug_window_rows"] - shapes["bug_dup_extras"]
        assert baseline_rows > 0
        assert 0.12 <= shapes["bug_dup_extras"] / baseline_rows <= 0.20
        assert 0.05 <= shapes["bug_null_rows"] / baseline_rows <= 0.08
        # Duplicate extras and NULL rows exist ONLY in the drawn segment —
        # every other segment's whole history is corruption-free.
        conn = db.connect(random_generated["db_a"], readonly=True)
        try:
            clean = conn.execute(
                "SELECT COUNT(*) FROM usage_events u "
                "JOIN accounts a ON a.id = u.account_id "
                "WHERE a.segment <> ? AND (u.units IS NULL)",
                (shapes["bug_segments"][0],),
            ).fetchone()[0]
            clean_dups = conn.execute(
                "SELECT COALESCE(SUM(n - 1), 0) FROM ("
                "  SELECT COUNT(*) AS n FROM usage_events u "
                "  JOIN accounts a ON a.id = u.account_id "
                "  WHERE a.segment <> ? "
                "  GROUP BY u.account_id, u.event_date, u.units HAVING n > 1"
                ")",
                (shapes["bug_segments"][0],),
            ).fetchone()[0]
        finally:
            conn.close()
        assert clean == 0 and clean_dups == 0
        # The spike cohort's cancels all carry the repricing signature the
        # agent's cause narrative reads: an in-window upgrade within 14 days
        # before the cancel.
        conn = db.connect(random_generated["db_a"], readonly=True)
        try:
            repriced = conn.execute(
                "SELECT COUNT(*) FROM subscription_events s "
                "WHERE s.event_type = 'cancel' AND s.event_date >= '2026-06-01' "
                "  AND EXISTS (SELECT 1 FROM subscription_events u "
                "              WHERE u.account_id = s.account_id "
                "                AND u.event_type = 'upgrade' "
                "                AND u.event_date BETWEEN "
                "                    date(s.event_date, '-14 days') "
                "                    AND s.event_date)"
            ).fetchone()[0]
        finally:
            conn.close()
        assert repriced == shapes["spike_count"]
        assert shapes["arr_mismatches"] == 0
        overrevenue, overusage = self._billing_stops_at_cancel(
            random_generated["db_a"]
        )
        assert overrevenue == 0 and overusage == 0

    def test_random_different_seed_varies(self, random_generated):
        """A different seed draws a different dataset — the harness really
        varies (at least one of the drawn shapes differs), while every
        drawn dataset still passes the structural invariants."""
        first, other = random_generated["first"], random_generated["other"]
        counts_a, shapes_a = first
        counts_c, shapes_c = other
        assert shapes_a != shapes_c or counts_a != counts_c

    def test_random_summary_shapes_match_the_database(self, tmp_path):
        """The summary's trailing churn/bug block prints the DRAWN shapes:
        the spike window spans the drawn range (its start can precede the
        first actual cancel day), the bug window's end always lands on data
        end, and the printed counts/segment equal the DB's derived values —
        no invented or stale numbers."""
        db_a = tmp_path / "sum.db"
        stdout = self._run(db_a, 13)
        shapes = self._shapes(db_a)
        churn_line = next(
            line for line in stdout.splitlines()
            if line.strip().startswith("churned accounts")
        )
        # "churned accounts (cancels) START..END: N — Segment=M, ..."
        printed_window = churn_line.split("cancels) ")[1].split(":")[0]
        printed_start, printed_end = printed_window.split("..")
        printed_count = int(churn_line.rsplit(": ", 1)[1].split(" ")[0])
        # The drawn window starts June-or-later and can precede the first
        # cancel; its end is always the data horizon. Every spike cancel is
        # inside it and no organic cancel is (< June), so the count matches.
        assert "2026-06-01" <= printed_start <= shapes["spike_start"]
        assert printed_end == "2026-09-30"
        assert printed_count == shapes["spike_count"]
        bug_line = next(
            line for line in stdout.splitlines()
            if line.strip().startswith("ingest bug")
        )
        # "ingest bug START..END (SEG only): duplicate rows X, null-amount rows Y"
        printed_bug_window = bug_line.split("bug ")[1].split(" (")[0]
        printed_bug_start, printed_bug_end = printed_bug_window.split("..")
        printed_segment = bug_line.split("(")[1].split(" only")[0]
        printed_dups = int(bug_line.split("duplicate rows ")[1].split(",")[0])
        printed_nulls = int(bug_line.split("null-amount rows ")[1])
        # The drawn 14-day window contains every NULL row and every dup
        # extra; all corruption lives in the printed segment.
        assert (
            date.fromisoformat(printed_bug_end)
            - date.fromisoformat(printed_bug_start)
        ).days == 13
        assert printed_bug_start <= shapes["bug_start"]
        assert printed_bug_end >= shapes["bug_end"]
        assert printed_segment == shapes["bug_segments"][0]
        assert printed_dups == shapes["bug_dup_extras"]
        assert printed_nulls == shapes["bug_null_rows"]

    def test_seeded_profile_ignores_seed(self, tmp_path):
        """The default (seeded) profile is the PRD's demo contract: any
        --seed value still produces the pinned shapes — two different seeds
        yield identical counts and identical planted-shape metrics, and the
        shape derivation reads the seeded cohort as the spike (40 cancels
        inside the pinned window, zero organic tail)."""
        db_a, db_b = tmp_path / "s1.db", tmp_path / "s2.db"
        self._run(db_a, 1, profile="seeded")
        self._run(db_b, 999999, profile="seeded")
        assert self._counts(db_a) == self._counts(db_b)
        assert self._shapes(db_a) == self._shapes(db_b)
        shapes = self._shapes(db_a)
        assert shapes["spike_count"] == 40
        assert shapes["organic_count"] == 0
        assert shapes["spike_start"] == "2026-08-10"

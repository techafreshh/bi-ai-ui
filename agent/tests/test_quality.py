"""Acceptance-contract tests for the data-quality checks (issue #6).

One test per acceptance clause of issue #6 (plus the threshold bands, scope
validation and default-scope semantics the plan pins), each with a docstring
naming the clause it proves. The seeded DB is driven through the generator's
real entrypoint as a subprocess (mirroring the runtime gate) with the same
session-fixture idiom as test_queries.py; quality.py opens only
db.connect(readonly=True) against db.DEFAULT_DB_PATH, so that module default
remains the single supported seam. The synthetic fixture builder
(quality_fixture.py) exists only to prove the checks discriminate: it
inserts exact row lists, so every share/coverage below is hand-computed
arithmetic, not a statistical expectation.
"""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

import db
import quality
from quality_fixture import build_fixture

AGENT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = AGENT_DIR / "scripts" / "generate_data.py"
RUN_TIMEOUT_SECONDS = 600

INJECTION = "2026-07-01'; DROP TABLE accounts;--"

# The Sept ingest-bug window the acceptance clause scopes to, and its 14
# calendar days as date objects for building fixture rows.
BUG_WINDOW = ("2026-09-08", "2026-09-21")
BUG_DAYS = [date(2026, 9, 8) + timedelta(days=i) for i in range(14)]

USAGE_METRIC = "api_calls"  # the schema's only metric value


def _by_name(checks):
    return {check["name"]: check for check in checks}


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
    """One seeded temp DB for the session; while it is in place, the check
    module's module default points at it so every run_checks call reads the
    seeded data (the default is restored afterwards)."""
    db_path = tmp_path_factory.mktemp("quality") / "seeded.db"
    _run_generator(db_path)
    original = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = db_path
    try:
        yield db_path
    finally:
        db.DEFAULT_DB_PATH = original


def _clean_rows(account_ids, days):
    """One usage row per (account, day): all units non-null and every
    (account, day) pair distinct, so no duplicates and no NULLs exist by
    construction."""
    return [
        (account, day.isoformat(), USAGE_METRIC, 100 * account + 7)
        for account in account_ids
        for day in days
    ]


def _null_row(account, day):
    """A single NULL-units usage row — its (account, day, metric, None)
    group never collides with a non-null row's group (units differ) nor
    with other NULL rows on distinct (account, day) slots."""
    return (account, day.isoformat(), USAGE_METRIC, None)


def test_run_checks_shape_contract(seeded_db):
    """Acceptance clause 1: given the seeded DB, run_checks() returns exactly
    3 checks named freshness / duplicates / nulls (fixed order), each with
    exactly {name, status, score}, a float score in [0, 1] and a status in
    {ok, warn, fail} — and the list JSON round-trips unchanged."""
    checks = quality.run_checks()
    assert len(checks) == 3
    assert [check["name"] for check in checks] == list(quality._CHECK_NAMES)
    for check in checks:
        assert set(check) == {"name", "status", "score"}
        assert isinstance(check["score"], float) and not isinstance(check["score"], bool)
        assert 0.0 <= check["score"] <= 1.0
        assert check["status"] in ("ok", "warn", "fail")
    assert json.loads(json.dumps(checks)) == checks


def test_seeded_bug_window_statuses(seeded_db):
    """Acceptance clause 2: scoped to the Sept ingest-bug window
    2026-09-08..2026-09-21, the seeded DB reads duplicates fail (exact-copy
    plants ~6% >> the 1% line), nulls fail (~2.7% > the 2% line) and
    freshness ok (all 14 days covered)."""
    checks = _by_name(quality.run_checks(*BUG_WINDOW))
    assert checks["duplicates"]["status"] == "fail"
    assert checks["nulls"]["status"] == "fail"
    assert checks["freshness"]["status"] == "ok"
    assert checks["freshness"]["score"] == 1.0
    assert checks["duplicates"]["score"] < 0.99
    assert checks["nulls"]["score"] < 0.99


def test_clean_fixture_all_statuses_ok(tmp_path, monkeypatch):
    """Acceptance clause 3: a clean synthetic DB (3 accounts, one
    non-null distinct (account, day) usage row for every day of the same
    14-day scope) reads all three statuses ok with every score exactly 1.0
    — the checks accept genuinely clean data, not just seeded levels."""
    db_path = build_fixture(tmp_path / "clean.db", _clean_rows((1, 2, 3), BUG_DAYS))
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    checks = quality.run_checks(*BUG_WINDOW)
    assert [check["status"] for check in checks] == ["ok", "ok", "ok"]
    assert [check["score"] for check in checks] == [1.0, 1.0, 1.0]


def test_checks_discriminate_buggy_fixture(tmp_path, monkeypatch):
    """Discrimination with exact arithmetic: 140 clean rows + 40 exact
    duplicate copies + 20 NULL-rows give dup share 40/200 = 0.2 and null
    share 20/200 = 0.1 — duplicates and nulls must fail with pinned scores
    0.8 / 0.9 while freshness stays ok (all 14 days covered)."""
    base = _clean_rows(range(1, 11), BUG_DAYS)  # 10 accounts x 14 days
    null_rows = [_null_row(1 + i % 10, BUG_DAYS[i % 14]) for i in range(20)]
    rows = base + base[:40] + null_rows
    assert len(rows) == 200

    db_path = build_fixture(tmp_path / "buggy.db", rows)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    checks = _by_name(quality.run_checks(*BUG_WINDOW))
    assert checks["duplicates"]["status"] == "fail"
    assert checks["duplicates"]["score"] == 0.8
    assert checks["nulls"]["status"] == "fail"
    assert checks["nulls"]["score"] == 0.9
    assert checks["freshness"]["status"] == "ok"


def test_warn_band_between_ok_and_fail(tmp_path, monkeypatch):
    """Threshold banding: sub-fail shares land on warn, not ok — (a) shares
    of 5 dup copies / 3 NULLs over 1016 rows (~0.49% and ~0.30%) warn while
    freshness stays ok; and a partially-covered scope — (b) 12 of 14 days —
    warns freshness with the exact coverage score 12/14 ≈ 0.8571."""
    warn_rows = _clean_rows(range(1, 73), BUG_DAYS)  # 72 x 14 = 1008
    warn_rows += _clean_rows((1,), BUG_DAYS[:5])  # first 5 exact dup copies
    warn_rows += [_null_row(a, d) for a, d in ((1, BUG_DAYS[0]), (2, BUG_DAYS[1]), (3, BUG_DAYS[2]))]

    db_path = build_fixture(tmp_path / "warn_shares.db", warn_rows)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    checks = _by_name(quality.run_checks(*BUG_WINDOW))
    assert checks["duplicates"]["status"] == "warn"
    assert checks["nulls"]["status"] == "warn"
    assert checks["freshness"]["status"] == "ok"
    assert checks["freshness"]["score"] == 1.0
    # Sanity-pins the band arithmetic: dup score is exactly the complement
    # of the 5/1016 share (in the warn interval, above the 0.2% warn line,
    # below the 1% fail line); likewise nulls for 3/1016.
    assert checks["duplicates"]["score"] == float(round(1.0 - 5 / 1016, 4))
    assert checks["nulls"]["score"] == float(round(1.0 - 3 / 1016, 4))

    gappy_rows = _clean_rows((1, 2, 3), BUG_DAYS[:12])  # 2 scope days uncovered
    db_path = build_fixture(tmp_path / "warn_gap.db", gappy_rows)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    checks = _by_name(quality.run_checks(*BUG_WINDOW))
    assert checks["freshness"]["status"] == "warn"
    assert checks["freshness"]["score"] == round(12 / 14, 4)
    assert [checks["duplicates"]["status"], checks["nulls"]["status"]] == ["ok", "ok"]


def test_freshness_fails_on_gappy_or_empty_scope(tmp_path, monkeypatch):
    """Freshness failure modes, with the sharing checks' vacuous-clean rule:
    events on only 3 of the 14 scope days miss 11 days (> 3) and fail; a
    scope with NO rows at all fails freshness (score 0.0) while duplicates
    and nulls are vacuously clean (nothing to corrupt) at score 1.0."""
    sparse_rows = _clean_rows((1, 2, 3), [BUG_DAYS[0], BUG_DAYS[4], BUG_DAYS[9]])
    db_path = build_fixture(tmp_path / "sparse.db", sparse_rows)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    checks = _by_name(quality.run_checks(*BUG_WINDOW))
    assert checks["freshness"]["status"] == "fail"
    assert checks["freshness"]["score"] == round(3 / 14, 4)

    db_path = build_fixture(tmp_path / "empty.db", [])
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)
    checks = _by_name(quality.run_checks(*BUG_WINDOW))
    assert [checks[name]["status"] for name in quality._CHECK_NAMES] == ["fail", "ok", "ok"]
    assert [checks[name]["score"] for name in quality._CHECK_NAMES] == [0.0, 1.0, 1.0]


def test_scope_validation_rejects_bad_input(seeded_db):
    """Contract edges: reversed windows, non-ISO and non-string bounds, the
    SQL-injection payload through both call forms, mis-shaped window pairs,
    and mixing window= with start/end all raise ValueError (before any SQL
    runs) — mirroring the query layer's input contract."""
    with pytest.raises(ValueError):
        quality.run_checks("2026-09-21", "2026-09-08")
    with pytest.raises(ValueError):
        quality.run_checks("September 8", "2026-09-21")
    with pytest.raises(ValueError):
        quality.run_checks(20260908, "2026-09-21")
    with pytest.raises(ValueError):
        quality.run_checks(True, "2026-09-21")
    with pytest.raises(ValueError):
        quality.run_checks(INJECTION, "2026-09-21")
    with pytest.raises(ValueError):
        quality.run_checks("2026-09-08", INJECTION)
    with pytest.raises(ValueError):
        quality.run_checks(window=INJECTION)
    with pytest.raises(ValueError):
        quality.run_checks(window=("2026-09-08",))
    with pytest.raises(ValueError):
        quality.run_checks(window=("2026-09-08", "2026-09-21", "2026-09-30"))
    with pytest.raises(ValueError):
        quality.run_checks(window=(INJECTION, "2026-09-21"))
    with pytest.raises(ValueError):
        quality.run_checks(*BUG_WINDOW, window=BUG_WINDOW)


def test_default_scope_is_trailing_30_days(seeded_db):
    """Default-scope semantics: run_checks() with no args equals an explicit
    call scoped to the trailing DEFAULT_SCOPE_DAYS ending at the latest
    observed event day (deterministic on the data, not a wall clock) — and
    on the seeded DB it reads ok / fail / warn in the fixed name order."""
    conn = db.connect(readonly=True)
    try:
        latest = conn.execute(
            "SELECT MAX(event_date) FROM ("
            " SELECT event_date FROM subscription_events"
            " UNION SELECT event_date FROM revenue_events"
            " UNION SELECT event_date FROM usage_events)"
        ).fetchone()[0]
    finally:
        conn.close()
    end = date.fromisoformat(latest)
    start = end - timedelta(days=quality.DEFAULT_SCOPE_DAYS - 1)
    assert end - start == timedelta(days=quality.DEFAULT_SCOPE_DAYS - 1)

    unscoped = quality.run_checks()
    assert unscoped == quality.run_checks(start.isoformat(), end.isoformat())
    assert [check["status"] for check in unscoped] == ["ok", "fail", "warn"]

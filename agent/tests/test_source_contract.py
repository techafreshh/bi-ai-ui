"""SQLite vs Postgres source contract (issue #80).

The same assertions run on both backends over the same seed — the four
`sample/*.csv` rows loaded into a throwaway sandbox (SQLite branch) and
into Postgres by `scripts/load_postgres.sh` (Postgres branch) — proving
template parity: `latest_event_day` is an ISO day and the KPI block
(`net_new_arr` in particular) is identical. The Postgres branch skips with
`SKIP: no DATASOURCE_URL` when no Postgres URL is configured, and probes
read-only discipline: a write attempt raises and row counts are unchanged.
"""

import csv
import os
import re
from pathlib import Path

import pytest

import datasets
import db
import quality
import queries
import sandbox

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "samples"

TABLES = (
    "accounts",
    "subscription_events",
    "revenue_events",
    "usage_events",
)

ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# August window: inside the samples' August revenue days and the
# Aug-11..28 repriced-churn cluster, so kpis/churn read non-trivial values.
WINDOW = ("2026-08-01", "2026-08-31")

SKIP_MESSAGE = "SKIP: no DATASOURCE_URL"


def _samples_max_day():
    """Ground-truth latest event day: MAX(event_date) over the three event
    CSVs, read straight from the files (no database involved)."""
    latest = ""
    for table in ("subscription_events", "revenue_events", "usage_events"):
        with open(SAMPLES_DIR / f"{table}.csv", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                latest = max(latest, row["event_date"])
    return latest


def _samples_net_new_arr(start, end):
    """Ground-truth net-new ARR in dollars over [start, end]: SUM of
    arr_delta_cents in the window, straight from subscription_events.csv."""
    total_cents = 0
    with open(SAMPLES_DIR / "subscription_events.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if start <= row["event_date"] <= end:
                total_cents += int(row["arr_delta_cents"])
    return round(total_cents / 100, 2)


@pytest.fixture(scope="session")
def outer_pg_url():
    """The operator's DATASOURCE_URL captured before any test isolation
    deletes it (sqlite_samples clears the var so the SQLite branch can
    never leak into Postgres, and vice versa)."""
    return os.getenv("DATASOURCE_URL", "")


@pytest.fixture
def sqlite_samples(monkeypatch, tmp_path):
    """Load the four samples/*.csv files into one throwaway sandbox and
    point the query layer at it — mirroring the test_datasets.py fixture
    idiom (registry save/restore, DB_PATH/DATASOURCE_URL cleared) so the
    SQLite branch reads exactly the seeded-shape rows and nothing leaks
    into other modules."""
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    monkeypatch.delenv("DATASOURCE_URL", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    rows_by_table = {}
    for table in TABLES:
        text = (SAMPLES_DIR / f"{table}.csv").read_text(encoding="utf-8")
        rows, _ = sandbox._parse_text(table, text, f"samples/{table}.csv")
        rows_by_table[table] = rows
    dest = sandbox.load_tables(rows_by_table, "contract-samples", sandbox_dir=tmp_path)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", dest)
    try:
        yield dest
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active


def _require_pg_url(outer_pg_url, monkeypatch):
    """Restore the operator's DATASOURCE_URL for one Postgres test (the
    sqlite fixture deletes it for isolation), or skip with the contract
    message when none was configured."""
    if not outer_pg_url.strip():
        pytest.skip(SKIP_MESSAGE)
    monkeypatch.setenv("DATASOURCE_URL", outer_pg_url)
    return outer_pg_url


def test_sqlite_latest_event_day_format_and_kpis(sqlite_samples):
    """Given the samples seed in SQLite, `latest_event_day` is an ISO day
    equal to the CSVs' max event_date, and `kpis` net_new_arr equals the
    CSV-ground-truth sum."""
    day = queries.latest_event_date()
    assert isinstance(day, str) and ISO_DAY_RE.match(day), day
    assert day == _samples_max_day()
    kpis = queries.kpis(*WINDOW)
    assert kpis["net_new_arr"] == _samples_net_new_arr(*WINDOW)
    assert isinstance(kpis["net_new_arr"], float)


def test_postgres_parity_or_skip(sqlite_samples, outer_pg_url, monkeypatch):
    """Given DATASOURCE_URL, Postgres answers identically to SQLite on the
    same seed (latest_event_day, full KPI block, quality checks, problem
    scope); without it the branch skips with `SKIP: no DATASOURCE_URL`."""
    _require_pg_url(outer_pg_url, monkeypatch)
    try:
        from data import postgres_source as pg
    except ModuleNotFoundError:  # pragma: no cover - script-style entry
        import postgres_source as pg  # type: ignore[no-redef]
    assert pg.use_postgres()

    lite_day = queries.latest_event_date()
    pg_day = pg.latest_event_date()
    assert isinstance(pg_day, str) and ISO_DAY_RE.match(pg_day), pg_day
    assert pg_day == lite_day

    lite_kpis = queries.kpis(*WINDOW)
    pg_kpis = pg.kpis(*WINDOW)
    assert pg_kpis["net_new_arr"] == lite_kpis["net_new_arr"]
    assert pg_kpis == lite_kpis

    assert pg.run_checks(*WINDOW) == quality.run_checks(*WINDOW)
    assert pg.recent_problem_scope() == quality.recent_problem_scope()


def test_postgres_read_only_or_skip(sqlite_samples, outer_pg_url, monkeypatch):
    """Given DATASOURCE_URL, a write through the Postgres source raises and
    row counts are unchanged afterwards (read-only discipline holds on the
    Postgres backend too); without it the branch skips."""
    _require_pg_url(outer_pg_url, monkeypatch)
    try:
        from data import PostgresSource
    except ModuleNotFoundError:  # pragma: no cover - script-style entry
        from postgres_source import PostgresSource  # type: ignore[no-redef]

    source = PostgresSource()
    conn = source.connect_ro()
    try:
        before = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in source.tables()
        }
    finally:
        conn.close()

    probe = source.connect_ro()
    try:
        with pytest.raises(Exception):
            probe.execute("CREATE TABLE pg_probe_write (id INTEGER)")
    finally:
        probe.close()

    check = source.connect_ro()
    try:
        after = {
            table: check.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in source.tables()
        }
        assert after == before
        assert check.execute(_probe_table_absent_sql()).fetchone()[0] == 0
    finally:
        check.close()


def _probe_table_absent_sql():
    """Count of relations named pg_probe_write: 0 proves the refused write
    left nothing behind."""
    return (
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = 'pg_probe_write'"
    )


def test_postgres_ro_guard_or_skip(sqlite_samples, outer_pg_url, monkeypatch):
    """Given DATASOURCE_URL, DATASOURCE_RO=0 refuses the Postgres query with
    a read-only error before any SQL runs; without a URL the branch skips."""
    _require_pg_url(outer_pg_url, monkeypatch)
    try:
        from data import postgres_source as pg
    except ModuleNotFoundError:  # pragma: no cover - script-style entry
        import postgres_source as pg  # type: ignore[no-redef]
    monkeypatch.setenv("DATASOURCE_RO", "0")
    with pytest.raises(RuntimeError, match="read-only"):
        pg.kpis(*WINDOW)


def test_datasets_lists_postgres_source_or_skip(
    sqlite_samples, outer_pg_url, monkeypatch
):
    """Given DATASOURCE_URL, GET /data/datasets lists one entry with
    `source` equal `postgres`; without it the branch skips."""
    _require_pg_url(outer_pg_url, monkeypatch)
    from fastapi.testclient import TestClient

    import server as server_module

    response = TestClient(server_module.app).get("/data/datasets")
    assert response.status_code == 200, response.text
    sources = [entry["source"] for entry in response.json()["datasets"]]
    assert "postgres" in sources

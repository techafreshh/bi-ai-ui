"""Slice parity contract tests (issue #91).

The release verifier (`scripts/verify_data_slice.py`) proves triple-query
parity live — NL churn total vs picker `GET /data/run` vs SQL/metric
`POST /data/query` spot-check within 1%. These tests pin the same legs at
the HTTP contract level over the shared session seed, without the network:

- the metric leg POST {metric, start, end} equals
  `queries.metric_value("gross_churned_arr", ...)` within 1e-9;
- the SQL spot-check SELECT of churned cents equals
  `queries.metric_value("gross_churned_arr", ...)` within 1e-9;
- the picker data_table mrr sum equals the SQL per-row mrr sum within
  1e-9 (same cohort, same rounding, same 50-row cap);
- the validation matrix (unknown metric, bare dates, start-after-end) is
  400 with the issue's substrings on the picker and metric legs, and an
  unreadable DB is 400 `no active dataset` on every leg;
- both query legs are read-only: repeated calls and the datasets listing
  are byte-identical before and after;
- the verifier's pure helpers (`within_pct`, `mrr_total`) hold their
  zero-base and junk-row contracts.
"""

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

import catalog
import datasets
import db
import queries
import server as server_module

client = TestClient(server_module.app)

REPO_ROOT = Path(__file__).resolve().parents[2]

_START = "2026-08-01"
_END = "2026-08-31"

_CENTS_SQL = (
    "SELECT COALESCE(SUM(a.arr_cents), 0) "
    "FROM subscription_events s JOIN accounts a ON a.id = s.account_id "
    f"WHERE s.event_type = 'cancel' AND s.event_date BETWEEN '{_START}' AND '{_END}'"
)

_MRR_SQL = (
    "SELECT ROUND(a.arr_cents / 1200.0, 2) AS mrr "
    "FROM subscription_events s JOIN accounts a ON a.id = s.account_id "
    f"WHERE s.event_type = 'cancel' AND s.event_date BETWEEN '{_START}' AND '{_END}' "
    "ORDER BY s.event_date, s.account_id"
)


def _verifier():
    """Import the stdlib-only verifier without running it (it guards its
    CLI under `__main__`), so the pure helpers stay under contract test."""
    spec = importlib.util.spec_from_file_location(
        "verify_data_slice", REPO_ROOT / "scripts" / "verify_data_slice.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_table_mrr_sum(payload):
    tables = [
        item for item in payload["items"] if item["type"] == "data_table"
    ]
    assert tables, "picker metric must carry a data_table"
    return sum(row["mrr"] for row in tables[0]["props"]["rows"])


def test_slice_metric_leg_matches_compiler(seeded_db):
    """The metric leg POST {metric, start, end} equals the compiler's
    gross_churned_arr value within 1e-9 — the read-only leg the verifier's
    parity line spot-checks alongside the SQL hatch."""
    response = client.post(
        "/data/query",
        json={"metric": "gross_churned_arr", "start": _START, "end": _END},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"metric", "start", "end", "value"}
    assert payload["metric"] == "gross_churned_arr"
    assert payload["start"] == _START
    assert payload["end"] == _END
    value = payload["value"]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = queries.metric_value("gross_churned_arr", _START, _END)
    assert isinstance(expected, (int, float)) and not isinstance(expected, bool)
    assert abs(value - expected) <= 1e-9


def test_slice_metric_leg_400_matrix(seeded_db):
    """Unknown metric, non-ISO dates and start-after-end are 400 with
    the run-validation substrings on the metric leg."""
    unknown = client.post(
        "/data/query",
        json={"metric": "bogus", "start": _START, "end": _END},
    )
    assert unknown.status_code == 400, unknown.text
    assert "unknown metric" in unknown.json()["error"]
    bad_date = client.post(
        "/data/query",
        json={"metric": "gross_churned_arr", "start": "2026-13-01", "end": _END},
    )
    assert bad_date.status_code == 400, bad_date.text
    assert "not a valid ISO date" in bad_date.json()["error"]
    flipped = client.post(
        "/data/query",
        json={"metric": "gross_churned_arr", "start": _END, "end": _START},
    )
    assert flipped.status_code == 400, flipped.text
    assert "is after" in flipped.json()["error"]
    missing = client.post("/data/query", json={"metric": "gross_churned_arr"})
    assert missing.status_code == 400, missing.text
    assert "error" in missing.json()


def test_slice_metric_empty_db_is_400_no_active_dataset(monkeypatch, tmp_path):
    """With no readable dataset the metric leg answers 400
    `no active dataset` instead of a degraded number."""
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", missing)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("DATASOURCE_URL", raising=False)
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    try:
        response = client.post(
            "/data/query",
            json={"metric": "gross_churned_arr", "start": _START, "end": _END},
        )
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active
    assert response.status_code == 400, response.text
    assert "no active dataset" in response.json()["error"]


def test_slice_metric_leg_is_read_only(seeded_db):
    """The metric leg mutates nothing observable: a repeated metric call
    is byte-identical and the datasets listing is unchanged."""
    body = {"metric": "gross_churned_arr", "start": _START, "end": _END}
    first = client.post("/data/query", json=body)
    assert first.status_code == 200, first.text
    listing_before = client.get("/data/datasets").json()
    second = client.post("/data/query", json=body)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert client.get("/data/datasets").json() == listing_before


def test_slice_sql_cents_match_metric_value(seeded_db):
    """The SQL spot-check SELECT of churned cents, divided by 100, equals
    the compiler's gross_churned_arr value within 1e-9 — the numeric leg
    the verifier's parity line rests on."""
    response = client.post("/data/query", json={"sql": _CENTS_SQL})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"columns", "rows"}
    cents = payload["rows"][0][0]
    assert isinstance(cents, (int, float)) and not isinstance(cents, bool)
    expected = queries.metric_value("gross_churned_arr", _START, _END)
    assert isinstance(expected, (int, float)) and not isinstance(expected, bool)
    assert abs(cents / 100.0 - expected) <= 1e-9


def test_slice_run_table_matches_sql_rows(seeded_db):
    """The picker data_table mrr sum equals the SQL per-row mrr sum within
    1e-9: same cohort window, same rounding, same 50-row cap."""
    run = client.get(
        "/data/run",
        params={
            "metric": "gross_churned_arr",
            "start": _START,
            "end": _END,
            "segment": "All",
        },
    )
    assert run.status_code == 200, run.text
    run_body = run.json()
    catalog.A2UILayout.model_validate(run_body)
    picker_total = _run_table_mrr_sum(run_body)

    sql = client.post("/data/query", json={"sql": _MRR_SQL})
    assert sql.status_code == 200, sql.text
    sql_body = sql.json()
    assert "mrr" in sql_body["columns"]
    sql_total = sum(row[0] for row in sql_body["rows"])
    assert abs(picker_total - sql_total) <= 1e-9


def test_slice_unknown_metric_is_400_unknown_metric(seeded_db):
    """metric=bogus on the picker leg is 400 with the unknown-metric
    substring (the run-validation idiom, first whitelist check)."""
    response = client.get(
        "/data/run",
        params={
            "metric": "bogus",
            "start": _START,
            "end": _END,
            "segment": "All",
        },
    )
    assert response.status_code == 400, response.text
    assert "unknown metric" in response.json()["error"]


def test_slice_bad_dates_are_400(seeded_db):
    """A non-calendar bound is 400 `not a valid ISO date` on the picker
    leg (month 13 never parses, before any SQL runs). Bare `YYYYMMDD`
    spellings are accepted and canonicalized by `date.fromisoformat`, so
    the rejection probe uses a truly invalid date."""
    response = client.get(
        "/data/run",
        params={
            "metric": "gross_churned_arr",
            "start": "2026-13-01",
            "end": "20260831",
            "segment": "All",
        },
    )
    assert response.status_code == 400, response.text
    assert "not a valid ISO date" in response.json()["error"]


def test_slice_out_of_order_window_is_400(seeded_db):
    """start after end is 400 with the `is after` substring on the picker
    leg."""
    response = client.get(
        "/data/run",
        params={
            "metric": "gross_churned_arr",
            "start": _END,
            "end": _START,
            "segment": "All",
        },
    )
    assert response.status_code == 400, response.text
    assert "is after" in response.json()["error"]


def test_slice_empty_db_is_400_on_both_legs(monkeypatch, tmp_path):
    """With no readable dataset both the picker and the SQL legs answer
    400 `no active dataset` instead of a degraded number."""
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", missing)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("DATASOURCE_URL", raising=False)
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    try:
        run = client.get(
            "/data/run",
            params={
                "metric": "gross_churned_arr",
                "start": _START,
                "end": _END,
                "segment": "All",
            },
        )
        sql = client.post("/data/query", json={"sql": _MRR_SQL})
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active
    assert run.status_code == 400, run.text
    assert "no active dataset" in run.json()["error"]
    assert sql.status_code == 400, sql.text
    assert "no active dataset" in sql.json()["error"]


def test_slice_sql_leg_is_read_only(seeded_db):
    """The SQL spot-check mutates nothing observable: a repeated SELECT is
    byte-identical and the datasets listing is unchanged across the call."""
    first = client.post("/data/query", json={"sql": _MRR_SQL})
    assert first.status_code == 200, first.text
    listing_before = client.get("/data/datasets").json()
    second = client.post("/data/query", json={"sql": _MRR_SQL})
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert client.get("/data/datasets").json() == listing_before


def test_slice_within_pct_zero_base_falls_back_to_absolute():
    """1% relative agreement with the absolute 1e-6 fallback on a zero
    base: empty-window comparisons stay exact instead of dividing."""
    within_pct = _verifier().within_pct
    assert within_pct(101.0, 100.0)
    assert within_pct(99.0, 100.0)
    assert not within_pct(102.0, 100.0)
    assert within_pct(0.0, 0.0)
    assert within_pct(1e-7, 0.0)
    assert not within_pct(1.0, 0.0)


def test_slice_mrr_total_skips_junk_rows():
    """The verifier's cohort total sums dict `mrr` cells and single-cell
    list rows, skipping bools and junk instead of raising."""
    mrr_total = _verifier().mrr_total
    rows = [
        {"mrr": 2000.0},
        {"mrr": "not-a-number"},
        {"mrr": True},
        {"segment": "Growth"},
        None,
    ]
    assert mrr_total(rows) == 2000.0
    assert mrr_total([[5.5], [], None]) == 5.5
    assert mrr_total([]) == 0.0
    assert "parity: ok" in (REPO_ROOT / "scripts" / "verify_data_slice.py").read_text(
        encoding="utf-8"
    )

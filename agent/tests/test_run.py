"""Black-box contract tests for GET /data/run (issue #81).

The manual-run endpoint composes picker values (metric + window +
segment) server-side into a validated A2UILayout with no decider: 200
with kpi_row first (plus line_chart for trendables, a 50-capped
data_table for cohorts), or 400 {"error"}.
"""

from fastapi.testclient import TestClient

import catalog
import datasets
import db
import server as server_module

client = TestClient(server_module.app)

WINDOW = {"metric": "nrr", "start": "2026-07-01", "end": "2026-09-30"}
SEGMENT = "Growth"


def test_run_nrr_returns_kpi_first_and_validates(seeded_db):
    """Acceptance: sample window + nrr is 200, items[0] is kpi_row, the
    body re-validates through catalog.A2UILayout, and no decisions key."""
    response = client.get("/data/run", params={**WINDOW, "segment": SEGMENT})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["v"] == 1
    assert body["layout"] == "grid"
    assert body["items"][0]["type"] == "kpi_row"
    catalog.A2UILayout.model_validate(body)
    assert "decisions" not in body
    assert body["slotCopy"]


def test_run_yaml_only_metric_resolves(seeded_db):
    """Acceptance: gross_churned_arr (registry-only, no new SQL) is 200
    with a kpi_row present."""
    response = client.get(
        "/data/run",
        params={
            "metric": "gross_churned_arr",
            "start": "2026-07-01",
            "end": "2026-09-30",
            "segment": SEGMENT,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(item["type"] == "kpi_row" for item in body["items"])
    catalog.A2UILayout.model_validate(body)


def test_run_bogus_metric_is_400_unknown_metric(seeded_db):
    """Acceptance: metric=bogus is 400 with error containing the
    unknown-metric substring."""
    response = client.get(
        "/data/run",
        params={
            "metric": "bogus",
            "start": "2026-07-01",
            "end": "2026-09-30",
            "segment": SEGMENT,
        },
    )
    assert response.status_code == 400, response.text
    assert "unknown metric" in response.json()["error"]


def test_run_missing_start_is_400(seeded_db):
    """A missing start bound is 400 {"error"}."""
    response = client.get(
        "/data/run", params={"metric": "nrr", "end": "2026-09-30"}
    )
    assert response.status_code == 400, response.text
    assert "error" in response.json()


def test_run_out_of_order_window_is_400(seeded_db):
    """start after end is 400 {"error"} naming the window."""
    response = client.get(
        "/data/run",
        params={
            "metric": "nrr",
            "start": "2026-09-30",
            "end": "2026-07-01",
            "segment": SEGMENT,
        },
    )
    assert response.status_code == 400, response.text
    assert "error" in response.json()


def test_run_bad_segment_is_400(seeded_db):
    """segment outside the allowlist is 400 {"error"}."""
    response = client.get(
        "/data/run",
        params={**WINDOW, "segment": "Bogus"},
    )
    assert response.status_code == 400, response.text
    assert "error" in response.json()


def test_run_cohort_table_rows_capped_at_50(seeded_db):
    """Cohort metrics carry a data_table with at most 50 rows."""
    response = client.get(
        "/data/run",
        params={
            "metric": "churn_rate",
            "start": "2026-07-01",
            "end": "2026-09-30",
            "segment": "All",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    tables = [item for item in body["items"] if item["type"] == "data_table"]
    assert tables, "cohort metric must carry a data_table"
    for table in tables:
        assert len(table["props"]["rows"]) <= 50
    catalog.A2UILayout.model_validate(body)


def test_run_no_active_dataset_is_400(monkeypatch, tmp_path):
    """With no readable dataset the endpoint answers 400 no active
    dataset instead of a degraded layout."""
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", missing)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("DATASOURCE_URL", raising=False)
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    try:
        response = client.get("/data/run", params={**WINDOW, "segment": SEGMENT})
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active
    assert response.status_code == 400, response.text
    assert "no active dataset" in response.json()["error"]

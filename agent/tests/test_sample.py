"""Sample-load route tests (issue #76).

POST /data/sample assembles ONE sandbox DB from all four SAMPLES_DIR/*.csv
files, registers it with source "sample" and activates it in one call —
200 {"active_id", "tables", "rows", "data_end"} with `data_end` the
union-MAX event_date. The sandbox scan and the samples dir are both swapped
to tmp dirs per test (seeded by copying the repo-root samples/*.csv), so
success writes and every 404/400's no-file assertion are both observable;
nothing here touches the network or the repo-root data/ tree.
"""

import csv
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import datasets
import db
import server as server_module

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_SAMPLES = REPO_ROOT / "samples"

client = TestClient(server_module.app)

EVENT_TABLES = ("subscription_events", "revenue_events", "usage_events")
EXPECTED_TABLES = [
    "accounts",
    "revenue_events",
    "subscription_events",
    "usage_events",
]


@pytest.fixture
def sample_dirs(monkeypatch, tmp_path):
    """Point the sandbox scan at a tmp dir and SAMPLES_DIR at a tmp copy of
    the repo samples; reset the process-global registry so the active id set
    here can never leak into other modules."""
    sandbox_dir = tmp_path / "sandboxes"
    sandbox_dir.mkdir()
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    for name in (
        "accounts.csv",
        "subscription_events.csv",
        "revenue_events.csv",
        "usage_events.csv",
    ):
        shutil.copy(REPO_SAMPLES / name, samples_dir / name)
    monkeypatch.setattr(db, "SANDBOX_DIR", sandbox_dir)
    monkeypatch.setattr(datasets, "SANDBOX_DIR", sandbox_dir)
    monkeypatch.setenv("SAMPLES_DIR", str(samples_dir))
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    try:
        yield {"sandbox_dir": sandbox_dir, "samples_dir": samples_dir}
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active


def _expected_rows_and_data_end(samples_dir):
    rows = 0
    data_end = None
    for name in (
        "accounts.csv",
        "subscription_events.csv",
        "revenue_events.csv",
        "usage_events.csv",
    ):
        with open(samples_dir / name, newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            data = list(reader)
            rows += len(data)
            if name != "accounts.csv":
                idx = header.index("event_date")
                for row in data:
                    if data_end is None or row[idx] > data_end:
                        data_end = row[idx]
    return rows, data_end


def test_sample_loads_four_tables_and_activates(sample_dirs):
    """POST /data/sample is 200 with the four-table shape; rows sums all
    four CSVs, data_end is the union-MAX event_date, and the sandbox file
    exists."""
    response = client.post("/data/sample")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"active_id", "tables", "rows", "data_end"}
    assert re.fullmatch(r"[0-9a-f]{32}", payload["active_id"]), payload
    assert payload["tables"] == EXPECTED_TABLES
    expected_rows, expected_end = _expected_rows_and_data_end(
        sample_dirs["samples_dir"]
    )
    assert payload["rows"] == expected_rows
    assert payload["rows"] > 0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["data_end"])
    assert payload["data_end"] == expected_end
    dest = sample_dirs["sandbox_dir"] / f"{payload['active_id']}.db"
    assert dest.exists()


def test_sample_registers_source_and_becomes_active(sample_dirs):
    """After the sample load, GET /data/datasets shows the id with source
    "sample" and the active pointer set; the entry profile matches the
    POST body."""
    payload = client.post("/data/sample").json()
    listing = client.get("/data/datasets")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["active_id"] == payload["active_id"]
    matches = [row for row in body["datasets"] if row["id"] == payload["active_id"]]
    assert len(matches) == 1, body["datasets"]
    entry = matches[0]
    assert entry["source"] == "sample"
    assert entry["tables"] == payload["tables"]
    assert entry["rows"] == payload["rows"]
    assert entry["data_end"] == payload["data_end"]


def test_sample_missing_dir_is_404_no_file(monkeypatch, tmp_path):
    """A nonexistent SAMPLES_DIR is 404 {"error"} with no `.db` created."""
    sandbox_dir = tmp_path / "sandboxes"
    sandbox_dir.mkdir()
    monkeypatch.setattr(db, "SANDBOX_DIR", sandbox_dir)
    monkeypatch.setattr(datasets, "SANDBOX_DIR", sandbox_dir)
    monkeypatch.setenv("SAMPLES_DIR", str(tmp_path / "no-such-dir"))
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    try:
        response = client.post("/data/sample")
        assert response.status_code == 404, response.text
        assert set(response.json()) == {"error"}
        assert list(sandbox_dir.glob("*.db")) == []
        assert client.get("/data/datasets").json()["active_id"] is None
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active


def test_sample_bad_csv_is_400_no_file(sample_dirs):
    """A corrupt sample CSV (bad ISO date) is 400 {"error"} with no `.db`
    file left behind and no activation."""
    bad = sample_dirs["samples_dir"] / "accounts.csv"
    lines = bad.read_text(encoding="utf-8").splitlines()
    header, *data = lines
    cols = header.split(",")
    idx = cols.index("created_at")
    parts = data[0].split(",")
    parts[idx] = "not-a-date"
    data[0] = ",".join(parts)
    bad.write_text("\n".join([header, *data]) + "\n", encoding="utf-8")
    response = client.post("/data/sample")
    assert response.status_code == 400, response.text
    assert set(response.json()) == {"error"}
    assert list(sample_dirs["sandbox_dir"].glob("*.db")) == []
    assert client.get("/data/datasets").json() == {
        "active_id": None,
        "datasets": [],
    }

"""Dataset list + active-switch tests (issue #75).

`GET /data/datasets` lists every sandbox with its profile
(`id`, `source`, `tables`, `rows`, `data_end`); `POST /data/active {id}`
moves the single process-wide active path so all subsequent queries read
only the newly active file, and unknown ids are 404 {"error"}. Fixture
DBs are fabricated directly with sandbox.load_bytes (no uploads, no
network); the registry's globals are saved and restored so no active id
leaks into other modules.
"""

import pytest
from fastapi.testclient import TestClient

import datasets
import db
import queries
import sandbox
import server as server_module

client = TestClient(server_module.app)

DATASET_A = "dataset-a"
DATASET_B = "dataset-b"

SUBSCRIPTION_COLUMNS = (
    "id,account_id,event_date,event_type,arr_delta_cents"
)
ROWS_A = (
    "1,1,2026-01-06,new,600000\n"
    "2,2,2026-01-07,new,600000\n"
)
ROWS_B = (
    "1,1,2026-01-06,new,600000\n"
    "2,2,2026-02-01,new,600000\n"
    "3,3,2026-02-15,new,600000\n"
    "4,4,2026-03-01,new,600000\n"
    "5,5,2026-03-10,new,600000\n"
)
DATA_END_A = "2026-01-07"
DATA_END_B = "2026-03-10"
EXPECTED_TABLES = [
    "accounts",
    "revenue_events",
    "subscription_events",
    "usage_events",
]


@pytest.fixture
def sandbox_registry(monkeypatch, tmp_path):
    """Point the registry scan at a tmp dir with two fixture sandboxes and
    reset the process-global registry afterwards, so the active id set here
    can never leak into test_server.py's empty-contract test (which runs
    after this module) or into DEFAULT_DB_PATH-fallback tests."""
    monkeypatch.setattr(datasets, "SANDBOX_DIR", tmp_path)
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    try:
        sandbox.load_bytes(
            "subscription_events",
            (SUBSCRIPTION_COLUMNS + "\n" + ROWS_A).encode(),
            DATASET_A,
            sandbox_dir=tmp_path,
        )
        sandbox.load_bytes(
            "subscription_events",
            (SUBSCRIPTION_COLUMNS + "\n" + ROWS_B).encode(),
            DATASET_B,
            sandbox_dir=tmp_path,
        )
        yield tmp_path
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active


def _entry(payload, dataset_id):
    matches = [row for row in payload["datasets"] if row["id"] == dataset_id]
    assert len(matches) == 1, payload["datasets"]
    return matches[0]


def test_empty_registry_lists_nothing(sandbox_registry):
    """No sandboxes, no active id: exactly {"active_id": None, "datasets": []}."""
    for path in sandbox_registry.glob("*.db"):
        path.unlink()
    response = client.get("/data/datasets")
    assert response.status_code == 200, response.text
    assert response.json() == {"active_id": None, "datasets": []}


def test_list_profiles_two_datasets(sandbox_registry):
    """Each sandbox lists its own profile: exact keys, upload source, all
    four tables sorted, per-file rows and data_end."""
    response = client.get("/data/datasets")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["active_id"] is None
    assert len(payload["datasets"]) == 2
    entry_a = _entry(payload, DATASET_A)
    entry_b = _entry(payload, DATASET_B)
    assert entry_a == {
        "id": DATASET_A,
        "source": "upload",
        "tables": EXPECTED_TABLES,
        "rows": 2,
        "data_end": DATA_END_A,
    }
    assert entry_b == {
        "id": DATASET_B,
        "source": "upload",
        "tables": EXPECTED_TABLES,
        "rows": 5,
        "data_end": DATA_END_B,
    }


def test_switch_active_moves_reads_to_new_file(sandbox_registry):
    """POST /data/active A is 200 {"active_id": A} with GET showing A
    active; switching to B rescopes the profile AND every queries.* read
    to B's file only (B's rows/data_end, not A's)."""
    response = client.post("/data/active", json={"id": DATASET_A})
    assert response.status_code == 200, response.text
    assert response.json() == {"active_id": DATASET_A}

    payload = client.get("/data/datasets").json()
    assert payload["active_id"] == DATASET_A
    assert _entry(payload, DATASET_A)["rows"] == 2
    assert queries.latest_event_date() == DATA_END_A
    assert queries.kpis("2026-01-01", "2026-03-31")["net_new_arr"] == 12000.0

    response = client.post("/data/active", json={"id": DATASET_B})
    assert response.status_code == 200, response.text
    assert response.json() == {"active_id": DATASET_B}

    payload = client.get("/data/datasets").json()
    assert payload["active_id"] == DATASET_B
    entry_b = _entry(payload, DATASET_B)
    assert entry_b["rows"] == 5
    assert entry_b["data_end"] == DATA_END_B
    assert queries.latest_event_date() == DATA_END_B
    kpis = queries.kpis("2026-01-01", "2026-03-31")
    assert kpis["net_new_arr"] == 30000.0
    assert kpis["accounts"] == 5


def test_unknown_id_is_404_and_keeps_active(sandbox_registry):
    """POST /data/active {"id": "nope"} is 404 with an "error" key and the
    active pointer stays where it was."""
    assert client.post("/data/active", json={"id": DATASET_A}).status_code == 200
    response = client.post("/data/active", json={"id": "nope"})
    assert response.status_code == 404, response.text
    assert "error" in response.json()
    assert client.get("/data/datasets").json()["active_id"] == DATASET_A
    assert datasets.get_active() == DATASET_A


@pytest.mark.parametrize(
    "body",
    [{}, {"id": ""}, {"id": "   "}, {"id": 7}, {"id": None}, {"id": "../evil"}],
)
def test_malformed_ids_are_404_not_422(sandbox_registry, body):
    """Missing, blank, non-string or path-shaped ids are 404 {"error"} —
    never 400/422 — and never move the active pointer."""
    response = client.post("/data/active", json=body)
    assert response.status_code == 404, (body, response.text)
    assert "error" in response.json()
    assert client.get("/data/datasets").json()["active_id"] is None


def test_unparseable_body_is_404(sandbox_registry):
    """A body that is not JSON at all is 404 {"error"}, not a 500."""
    response = client.post("/data/active", content=b"not json")
    assert response.status_code == 404, response.text
    assert "error" in response.json()


def test_register_validates_and_lists_source(sandbox_registry):
    """register() pins ids to [A-Za-z0-9_-]+ and sources to the known set;
    a registered id is activatable and lists with its source."""
    with pytest.raises(ValueError):
        datasets.register("../evil", sandbox_registry / "x.db")
    with pytest.raises(ValueError):
        datasets.register("ok-id", sandbox_registry / "x.db", source="s3")
    datasets.register(DATASET_A, sandbox_registry / f"{DATASET_A}.db", source="sample")
    assert datasets.set_active(DATASET_A) is True
    assert db.connect() is not None
    db.connect().close()
    entry = _entry(client.get("/data/datasets").json(), DATASET_A)
    assert entry["source"] == "sample"


def test_profile_missing_path_is_empty(sandbox_registry):
    """Profiling a missing file reports empty (tables [], rows 0, data_end
    None) instead of creating a database file."""
    missing = sandbox_registry / "ghost.db"
    assert datasets.profile_db(missing) == {
        "tables": [],
        "rows": 0,
        "data_end": None,
    }
    assert not missing.exists()

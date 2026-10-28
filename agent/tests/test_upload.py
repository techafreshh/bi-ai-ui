"""Upload route tests (issue #74).

POST /data/upload takes multipart `file` + `table`, validates everything in
memory (table allowlist, UPLOAD_MAX_MB size cap, exact-header allowlist,
strict int/ISO-date coercion) before sandbox.py creates any file, and
answers 201 {"id", "table", "rows", "data_end"} or 400 {"error"}. The
sandbox directory is swapped to a tmp dir per test so success writes and
every 400's no-file assertion are both observable; nothing here touches the
network or the repo-root data/ tree.
"""

import csv
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import server as server_module

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_ACCOUNTS = REPO_ROOT / "samples" / "accounts.csv"

client = TestClient(server_module.app)


@pytest.fixture
def sandbox_dir(monkeypatch, tmp_path):
    """Point the sandbox home at a tmp dir: writes land here, and an empty
    glob proves a 400 created no `.db` file."""
    monkeypatch.setattr(db, "SANDBOX_DIR", tmp_path)
    return tmp_path


def _post(body, table="accounts", filename="accounts.csv"):
    """POST one upload; `table=None` omits the form field entirely."""
    files = {"file": (filename, body, "text/csv")}
    data = {} if table is None else {"table": table}
    return client.post("/data/upload", files=files, data=data)


def _csv_rows():
    with open(SAMPLES_ACCOUNTS, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def test_upload_accounts_returns_201_with_id_rows_data_end(sandbox_dir):
    """Given samples/accounts.csv, POST /data/upload with table=accounts is
    201 with non-empty id, rows > 0 and a YYYY-MM-DD data_end; the sandbox
    file exists and reads back read-only with the same row count."""
    response = _post(SAMPLES_ACCOUNTS.read_bytes())
    assert response.status_code == 201, response.text
    payload = response.json()
    assert set(payload) == {"id", "table", "rows", "data_end"}
    assert re.fullmatch(r"[0-9a-f]{32}", payload["id"]), payload["id"]
    assert payload["table"] == "accounts"
    header, *data = _csv_rows()
    assert payload["rows"] == len(data)
    assert payload["rows"] > 0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["data_end"])
    assert payload["data_end"] == max(row[header.index("created_at")] for row in data)
    dest = sandbox_dir / f"{payload['id']}.db"
    assert dest.exists()
    conn = db.connect_ro(str(dest))
    try:
        count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    finally:
        conn.close()
    assert count == payload["rows"]


def test_upload_unknown_column_header_is_400(sandbox_dir):
    """A CSV headed `foo,bar` is 400 with `unknown column` in `error`, and
    creates no `.db` file."""
    response = _post(b"foo,bar\n1,2\n")
    assert response.status_code == 400, response.text
    payload = response.json()
    assert set(payload) == {"error"}
    assert "unknown column" in payload["error"]
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_empty_file_is_400_empty(sandbox_dir):
    """A zero-byte body is 400 with `empty` in `error`, no file created."""
    response = _post(b"")
    assert response.status_code == 400, response.text
    assert "empty" in response.json()["error"]
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_header_only_is_400_empty(sandbox_dir):
    """A header with zero data rows is 400 with `empty` in `error`."""
    response = _post(b"id,name,segment,plan,arr_cents,created_at\n")
    assert response.status_code == 400, response.text
    assert "empty" in response.json()["error"]
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_oversize_is_400_limit(monkeypatch, sandbox_dir):
    """With UPLOAD_MAX_MB forced to 0, even the small sample body is 400
    with the error naming the limit — and no file is created."""
    monkeypatch.setenv("UPLOAD_MAX_MB", "0")
    response = _post(SAMPLES_ACCOUNTS.read_bytes())
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert "too large" in error or "limit" in error, error
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_non_numeric_cap_falls_back_to_default(monkeypatch, sandbox_dir):
    """A non-numeric UPLOAD_MAX_MB falls back to 25 MB: the sample upload
    still lands 201 instead of 500ing or mis-capping."""
    monkeypatch.setenv("UPLOAD_MAX_MB", "not-a-number")
    response = _post(SAMPLES_ACCOUNTS.read_bytes())
    assert response.status_code == 201, response.text


def test_upload_unknown_table_is_400_with_allowed_set(sandbox_dir):
    """An unknown `table` is 400 naming the allowed set, no file created."""
    response = _post(SAMPLES_ACCOUNTS.read_bytes(), table="ledger")
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert "unknown table" in error
    assert "accounts" in error
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_missing_table_is_400_not_422(sandbox_dir):
    """Omitting the `table` form field is 400 {"error": ...}, never 422."""
    response = _post(SAMPLES_ACCOUNTS.read_bytes(), table=None)
    assert response.status_code == 400, response.text
    assert set(response.json()) == {"error"}
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_bad_iso_date_is_400(sandbox_dir):
    """A non-ISO `created_at` is 400, no file created."""
    header, *data = _csv_rows()
    data[0] = [*data[0][:5], "not-a-date"]
    lines = [",".join(header)] + [",".join(row) for row in data]
    response = _post(("\n".join(lines) + "\n").encode())
    assert response.status_code == 400, response.text
    assert set(response.json()) == {"error"}
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_bad_integer_is_400(sandbox_dir):
    """A non-integer `arr_cents` is 400, no file created."""
    header, *data = _csv_rows()
    data[0] = [data[0][0], data[0][1], data[0][2], data[0][3], "lots", data[0][5]]
    lines = [",".join(header)] + [",".join(row) for row in data]
    response = _post(("\n".join(lines) + "\n").encode())
    assert response.status_code == 400, response.text
    assert set(response.json()) == {"error"}
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_rejects_non_utf8_body(sandbox_dir):
    """Undecodable bytes are 400, no file created."""
    response = _post(b"id,name\n\xff\xfe\x00bad\n")
    assert response.status_code == 400, response.text
    assert list(sandbox_dir.glob("*.db")) == []


def test_upload_usage_events_accepts_empty_units(sandbox_dir):
    """The nullable `units` column maps `\"\"` to NULL: a one-row
    usage_events upload lands 201 with rows == 1."""
    body = b"id,account_id,event_date,metric,units\n1,7,2026-08-05,api_calls,\n"
    response = _post(body, table="usage_events", filename="usage_events.csv")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload == {
        "id": payload["id"],
        "table": "usage_events",
        "rows": 1,
        "data_end": "2026-08-05",
    }
    assert (sandbox_dir / f"{payload['id']}.db").exists()


def test_upload_does_not_activate_dataset(monkeypatch, sandbox_dir):
    """The upload writes the sandbox file but never switches DB_PATH: the
    active dataset stays None (activation is a later issue)."""
    monkeypatch.delenv("DB_PATH", raising=False)
    response = _post(SAMPLES_ACCOUNTS.read_bytes())
    assert response.status_code == 201, response.text
    assert db.get_active_path() is None

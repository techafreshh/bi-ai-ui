"""Sandbox DB helper tests (issue #73).

The loader builds a per-dataset `data/sandboxes/<id>.db` from one CSV
table with the full four-table schema and the three `event_date` indexes;
`db.get_active_path()` resolves the active sandbox from `DB_PATH` (empty
means no active DB, creating nothing) and `db.connect_ro()` reads it
read-only. Pure file + SQLite assertions, no network.
"""

import csv
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import db
import sandbox

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parent

EXPECTED_INDEXES = {
    "idx_subscription_events_event_date",
    "idx_revenue_events_event_date",
    "idx_usage_events_event_date",
}


def _accounts_csv(tmp_path):
    """The committed repo-root samples/accounts.csv when present (the
    issue's Given), else a tmp CSV with the identical header."""
    committed = REPO_ROOT / "samples" / "accounts.csv"
    if committed.exists():
        return committed
    candidate = tmp_path / "accounts.csv"
    with open(candidate, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "name", "segment", "plan", "arr_cents", "created_at"])
        writer.writerow([1, "Acme Labs 001", "Growth", "Scale", 2400000, "2026-01-06"])
    return candidate


def _csv_row_count(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.reader(handle)) - 1


def test_sandbox_loader_creates_file_and_three_event_date_indexes(tmp_path):
    """Load samples/accounts.csv as id `t1`: the sandbox file exists and
    sqlite_master holds exactly the three event_date indexes."""
    dest = sandbox.load_csv(_accounts_csv(tmp_path), "t1", sandbox_dir=tmp_path)
    assert dest == tmp_path / "t1.db"
    assert dest.exists()
    conn = sqlite3.connect(str(dest))
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert EXPECTED_INDEXES <= names
    assert {name for name in names if "event_date" in name} == EXPECTED_INDEXES


def test_get_active_path_none_when_unset_or_empty(monkeypatch, tmp_path):
    """With DB_PATH unset, empty or whitespace-only, get_active_path()
    returns None and creates no file anywhere."""
    monkeypatch.delenv("DB_PATH", raising=False)
    assert db.get_active_path() is None
    monkeypatch.setenv("DB_PATH", "")
    assert db.get_active_path() is None
    monkeypatch.setenv("DB_PATH", "   ")
    assert db.get_active_path() is None
    assert list(tmp_path.iterdir()) == []


def test_connect_ro_reads_active_sandbox(monkeypatch, tmp_path):
    """With DB_PATH pointed at the built sandbox, connect_ro() opens it
    (mode=ro URI) and SELECT COUNT(*) FROM accounts matches the CSV rows;
    a write through it raises OperationalError."""
    csv_path = _accounts_csv(tmp_path)
    dest = sandbox.load_csv(csv_path, "t1", sandbox_dir=tmp_path)
    monkeypatch.setenv("DB_PATH", str(dest))
    assert db.get_active_path() == Path(str(dest))
    conn = db.connect_ro()
    try:
        count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        assert count == _csv_row_count(csv_path)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO accounts (id, name, segment, plan, arr_cents,"
                " created_at) VALUES (999999, 'x', 'Growth', 'Scale', 1,"
                " '2026-01-01')"
            )
    finally:
        conn.close()


def test_loader_rejects_bad_id_and_bad_header(monkeypatch, tmp_path):
    """A `../` dataset id and a CSV with an unknown column both raise
    ValueError and create no `.db` file."""
    csv_path = _accounts_csv(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        sandbox.load_csv(csv_path, "../evil", sandbox_dir=tmp_path)
    bad = tmp_path / "accounts.csv"
    with open(csv_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    rows[0] = [*rows[0], "hacker_column"]
    with open(bad, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    with pytest.raises(ValueError):
        sandbox.load_csv(bad, "t2", sandbox_dir=tmp_path)
    assert list(tmp_path.glob("*.db")) == []


def test_loader_cli_builds_sandbox_as_subprocess(tmp_path):
    """The module entrypoint (`python sandbox.py <csv> <id>`) builds the
    sandbox through a real subprocess, mirroring the generator probes."""
    csv_path = _accounts_csv(tmp_path)
    dest = db.SANDBOX_DIR / "cli-t1.db"
    if dest.exists():
        dest.unlink()
    try:
        result = subprocess.run(
            [sys.executable, str(AGENT_DIR / "sandbox.py"), str(csv_path), "cli-t1"],
            cwd=AGENT_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert dest.exists(), f"{result.stdout}\n{result.stderr}"
    finally:
        if dest.exists():
            dest.unlink()

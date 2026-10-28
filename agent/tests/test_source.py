"""Source protocol plus verbatim SqliteSource tests (issue #78).

`SqliteSource` on a seeded sandbox returns `latest_event_day()` equal to
`MAX(event_date)` as an ISO-date string, returns None on an eventless DB,
and `tables()` lists the present known tables sorted; `connect_ro()` is
read-only (a write raises `sqlite3.OperationalError`). A contract test
pins the issue's grep acceptance: `graph.py` and `quality.py` hold zero
`import sqlite3` matches and import `from agent.data`.
"""

import re
import sqlite3
from pathlib import Path

import pytest
from quality_fixture import build_fixture

import db
from data import SqliteSource
from data.protocol import Source

AGENT_DIR = Path(__file__).resolve().parents[1]

ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EXPECTED_TABLES = [
    "accounts",
    "revenue_events",
    "subscription_events",
    "usage_events",
]


def _direct_max(path):
    """Ground truth `MAX(event_date)` over the three event tables."""
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT MAX(d) FROM ("
            "SELECT MAX(event_date) AS d FROM subscription_events"
            " UNION ALL SELECT MAX(event_date) FROM revenue_events"
            " UNION ALL SELECT MAX(event_date) FROM usage_events"
            ")"
        ).fetchone()[0]
    finally:
        conn.close()


def test_latest_event_day_matches_max(seeded_db):
    """Given the seeded DB, `SqliteSource(path).latest_event_day()`
    returns a `YYYY-MM-DD` string equal to `MAX(event_date)`."""
    source = SqliteSource(seeded_db)
    day = source.latest_event_day()
    assert isinstance(day, str) and ISO_DAY_RE.match(day), day
    assert day == _direct_max(seeded_db)


def test_latest_event_day_default_path_matches_seed(seeded_db):
    """Given the seeded DB as the active default, the no-arg source
    resolves the same anchor (registry/DB_PATH/DEFAULT_DB_PATH)."""
    assert SqliteSource().latest_event_day() == _direct_max(seeded_db)


def test_latest_event_day_none_on_eventless_db(tmp_path):
    """Given an eventless DB (schema, no rows), `latest_event_day()`
    returns None instead of raising."""
    db_path = build_fixture(tmp_path / "empty.db", [])
    assert SqliteSource(db_path).latest_event_day() is None


def test_tables_lists_present_known_tables_sorted(seeded_db, tmp_path):
    """Given the seeded DB, `tables()` lists the four known tables
    sorted; given a file holding only `accounts`, only that one."""
    assert SqliteSource(seeded_db).tables() == EXPECTED_TABLES
    partial = tmp_path / "partial.db"
    conn = db.connect(partial)
    try:
        conn.execute(
            "CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT)"
        )
        conn.execute("CREATE TABLE junk (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    assert SqliteSource(partial).tables() == ["accounts"]


def test_connect_ro_is_read_only(seeded_db):
    """Given a source, `connect_ro()` opens read-only: a write raises
    `sqlite3.OperationalError` and a missing file fails instead of
    being created."""
    source = SqliteSource(seeded_db)
    conn = source.connect_ro()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE probe_write (id INTEGER)")
    finally:
        conn.close()
    missing = seeded_db.parent / "definitely-missing-78.db"
    if missing.exists():
        missing.unlink()
    with pytest.raises(sqlite3.OperationalError):
        SqliteSource(missing).connect_ro()
    assert not missing.exists()


def test_source_protocol_shape():
    """`Source` is a runtime-checkable protocol with the three members,
    and `SqliteSource` structurally implements it."""
    assert issubclass(SqliteSource, Source)
    assert isinstance(SqliteSource(), Source)
    for member in ("connect_ro", "tables", "latest_event_day"):
        assert hasattr(Source, member), member


def test_graph_and_quality_import_only_the_protocol():
    """Acceptance grep contract: `graph.py` and `quality.py` hold zero
    `import sqlite3` matches and import `from agent.data`; the package
    defines `class Source` / `class SqliteSource`."""
    for name in ("graph.py", "quality.py"):
        text = (AGENT_DIR / name).read_text(encoding="utf-8")
        assert "import sqlite3" not in text, name
        assert "from agent.data" in text, name
    assert re.search(r"^class Source\b", (AGENT_DIR / "data" / "protocol.py").read_text(
        encoding="utf-8"), re.MULTILINE)
    assert re.search(r"^class SqliteSource\b", (AGENT_DIR / "data" / "sqlite_source.py").read_text(
        encoding="utf-8"), re.MULTILINE)

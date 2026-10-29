"""Black-box contract tests for POST /data/query (issue #83).

The guarded SQL escape hatch allows exactly one leading SELECT, blocks
mutating/probing keywords and `;`, caps rows via SQL_ROW_LIMIT and runs on
the read-only sandbox connection: 200 {"columns", "rows"} or 400 {"error"}.
"""

from fastapi.testclient import TestClient

import datasets
import db
import server as server_module

client = TestClient(server_module.app)

GROUP_BY = "SELECT segment, COUNT(*) FROM accounts GROUP BY segment"


def test_query_group_by_returns_200_with_segment_capped(seeded_db):
    """Acceptance: the sample group-by is 200 with `segment` in columns
    and at most 50 rows."""
    response = client.post("/data/query", json={"sql": GROUP_BY})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"columns", "rows"}
    assert isinstance(body["columns"], list)
    assert all(isinstance(name, str) for name in body["columns"])
    assert "segment" in body["columns"]
    assert isinstance(body["rows"], list)
    assert all(isinstance(row, list) for row in body["rows"])
    assert len(body["rows"]) <= 50


def test_query_drop_is_400_blocked_and_table_survives(seeded_db):
    """Acceptance: DROP is 400 with `blocked` in error and accounts stays
    queryable afterwards."""
    response = client.post("/data/query", json={"sql": "DROP TABLE accounts"})
    assert response.status_code == 400, response.text
    assert "blocked" in response.json()["error"]
    followup = client.post("/data/query", json={"sql": GROUP_BY})
    assert followup.status_code == 200, followup.text
    assert "segment" in followup.json()["columns"]


def test_query_multi_statement_is_400_single_statement(seeded_db):
    """Acceptance: a semicolon pair is 400 with `single statement`."""
    response = client.post(
        "/data/query",
        json={"sql": "SELECT * FROM accounts; SELECT * FROM accounts"},
    )
    assert response.status_code == 400, response.text
    assert "single statement" in response.json()["error"]


def test_query_blocked_keywords_are_400(seeded_db):
    """Each mutating/probing keyword is 400 with `blocked` in error."""
    payloads = [
        "INSERT INTO accounts SELECT * FROM accounts",
        "UPDATE accounts SET segment = 'X'",
        "DELETE FROM accounts",
        "DROP TABLE accounts",
        "ALTER TABLE accounts ADD COLUMN foo TEXT",
        "CREATE TABLE foo (id INTEGER)",
        "ATTACH DATABASE ':memory:' AS mem",
        "PRAGMA table_info(accounts)",
        "SELECT delete FROM accounts",
    ]
    for sql in payloads:
        response = client.post("/data/query", json={"sql": sql})
        assert response.status_code == 400, (sql, response.text)
        assert "blocked" in response.json()["error"], sql


def test_query_insert_and_pragma_are_400_blocked(seeded_db):
    """INSERT and PRAGMA spellings are 400 `blocked`, case-insensitively."""
    for sql in ("insert into accounts SELECT * FROM accounts", "pragma table_info(accounts)"):
        response = client.post("/data/query", json={"sql": sql})
        assert response.status_code == 400, (sql, response.text)
        assert "blocked" in response.json()["error"], sql


def test_query_non_select_leading_is_400_blocked(seeded_db):
    """WITH/CTE and other non-SELECT leaders never pass the gate."""
    for sql in (
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "UPDATE accounts SET segment = 'X'",
        "  delete from accounts",
    ):
        response = client.post("/data/query", json={"sql": sql})
        assert response.status_code == 400, (sql, response.text)
        assert "blocked" in response.json()["error"], sql


def test_query_missing_empty_and_non_string_sql_is_400(seeded_db):
    """Missing, empty and non-string `sql` bodies are 400 {"error"}."""
    cases = [
        {},
        {"sql": ""},
        {"sql": "   "},
        {"sql": None},
        {"sql": 42},
        {"sql": ["SELECT 1"]},
    ]
    for body in cases:
        response = client.post("/data/query", json=body)
        assert response.status_code == 400, (body, response.text)
        assert set(response.json()) == {"error"}, body


def test_query_semicolon_beats_keyword_check(seeded_db):
    """The `;` check runs first: a two-SELECT payload reports
    `single statement`, not `blocked`."""
    response = client.post(
        "/data/query",
        json={"sql": "SELECT * FROM accounts; SELECT * FROM accounts"},
    )
    assert response.status_code == 400, response.text
    assert "single statement" in response.json()["error"]


def test_query_row_cap_follows_sql_row_limit(monkeypatch, seeded_db):
    """SQL_ROW_LIMIT=1 caps the wrapped query at one row."""
    monkeypatch.setenv("SQL_ROW_LIMIT", "1")
    response = client.post("/data/query", json={"sql": "SELECT * FROM accounts"})
    assert response.status_code == 200, response.text
    assert len(response.json()["rows"]) <= 1


def test_query_no_active_dataset_is_400(monkeypatch, tmp_path):
    """With no readable dataset the endpoint answers 400 no active
    dataset instead of 500ing the connect."""
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", missing)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("DATASOURCE_URL", raising=False)
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets._BY_ID.clear()
    datasets._ACTIVE_ID = None
    try:
        response = client.post("/data/query", json={"sql": GROUP_BY})
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active
    assert response.status_code == 400, response.text
    assert "no active dataset" in response.json()["error"]

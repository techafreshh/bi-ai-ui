"""Store tests: determinism, seed invalidation, and the planted shapes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import store


def _canonical(path: Path) -> dict:
    """Full row-set snapshot of a database file (content, not just counts)."""
    conn = store.connect(path)
    try:
        return {
            "accounts": [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id")],
            "arr_daily": [dict(r) for r in conn.execute("SELECT * FROM arr_daily ORDER BY day")],
            "usage": [dict(r) for r in conn.execute("SELECT * FROM usage_events ORDER BY id")],
        }
    finally:
        conn.close()


def test_regeneration_is_byte_deterministic(tmp_path):
    """Two independent builds produce identical row content (seed 42)."""
    env = {k: v for k, v in os.environ.items() if k != "BI_DATA_DIR"}
    one, two = tmp_path / "one", tmp_path / "two"
    with mock.patch.dict(os.environ, {**env, "BI_DATA_DIR": str(one)}, clear=True):
        store.write_db(one / "app.db")
    with mock.patch.dict(os.environ, {**env, "BI_DATA_DIR": str(two)}, clear=True):
        store.write_db(two / "app.db")
    assert _canonical(one / "app.db") == _canonical(two / "app.db")


def test_planted_shape_counts(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "BI_DATA_DIR"}
    with mock.patch.dict(os.environ, {**env, "BI_DATA_DIR": str(tmp_path)}, clear=True):
        counts = store.write_db(tmp_path / "app.db")
    assert counts["accounts"] == store.N_ACCOUNTS
    assert counts["churn_cohort"] == store.CHURN_ACCOUNTS
    # 24 months of daily revenue events.
    assert counts["arr_daily"] == (store.HORIZON - store.DATA_START).days + 1


def test_in_memory_generation_order_stable():
    accounts_a, arr_a, usage_a = store.build_rows()
    accounts_b, arr_b, usage_b = store.build_rows()
    assert json.dumps(accounts_a, sort_keys=True, default=str) == json.dumps(
        accounts_b, sort_keys=True, default=str
    )
    assert json.dumps(arr_a, sort_keys=True, default=str) == json.dumps(
        arr_b, sort_keys=True, default=str
    )
    assert json.dumps(usage_a, sort_keys=True, default=str) == json.dumps(
        usage_b, sort_keys=True, default=str
    )


def test_ensure_seeded_rebuilds_when_seed_version_drifts(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "BI_DATA_DIR"}
    db = tmp_path / "app.db"
    with mock.patch.dict(os.environ, {**env, "BI_DATA_DIR": str(tmp_path)}, clear=True):
        store.write_db(db)
        conn = store.connect()
        with conn:
            conn.execute("UPDATE meta SET value = 'stale' WHERE key = 'seed_version'")
        conn.close()
        counts = store.ensure_seeded()  # stale version triggers a rebuild, not a skip
        conn = store.connect()
        version = conn.execute("SELECT value FROM meta WHERE key = 'seed_version'").fetchone()[0]
        conn.close()
    assert version == store.SEED_VERSION
    assert counts["accounts"] == store.N_ACCOUNTS


def test_cli_reset_returns_zero(capsys):
    assert store.main(["--reset"]) == 0
    assert "seeded" in capsys.readouterr().out

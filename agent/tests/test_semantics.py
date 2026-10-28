"""Contract tests for the semantics registry (issue #77).

The YAML file declares the eight queryable metrics, the stdlib loader
validates name/column at import (fail fast on typos), queries.py compiles
each name onto the existing static SQL, and compact_snapshot_summary
inlines the definitions for per-decider grounding.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import db
import queries
import questions
import semantics
from semantics import load_metrics

AGENT_DIR = Path(__file__).resolve().parents[1]


def test_registry_loads_eight_entries_with_four_keys():
    """Acceptance: load_metrics() returns 8 entries each with exactly the
    keys name/definition/grain/format, including gross_churned_arr and
    active_accounts_eom — and queries.py compiled the same file at import."""
    rows = load_metrics()
    assert len(rows) == 8
    for row in rows:
        assert set(row) == {"name", "definition", "grain", "format"}
    names = [row["name"] for row in rows]
    assert "gross_churned_arr" in names
    assert "active_accounts_eom" in names
    assert queries.METRICS == rows


def test_yaml_has_exactly_eight_name_lines():
    """Grep-level pin (mirrors the acceptance `grep \"name:\" | wc -l`):
    the file text holds exactly 8 lines starting with an optional indent
    plus `- name:` — one per entry, no other `name:` line anywhere."""
    lines = semantics.METRICS_FILE.read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in lines if "name:" in line) == 8
    assert (
        sum(1 for line in lines if line.lstrip().startswith("- name:")) == 8
    )


def _tampered_copy(tmp_path, *, name=None, column=None):
    """Shipped file with the first entry's name/column swapped — both
    replacements land in the same (first) entry, so a doubly-bogus copy
    carries both faults at once."""
    text = semantics.METRICS_FILE.read_text(encoding="utf-8")
    if name is not None:
        text = text.replace("name: net_new_arr", f"name: {name}", 1)
    if column is not None:
        text = text.replace("column: arr_delta_cents", f"column: {column}", 1)
    target = tmp_path / "metrics.yaml"
    target.write_text(text, encoding="utf-8")
    return target


def test_bogus_column_raises_unknown_column(tmp_path):
    """Acceptance: a YAML entry with `name: bogus` on an unknown column
    raises ValueError containing `unknown column` (the column check runs
    before the name check) without touching any database."""
    target = _tampered_copy(tmp_path, name="bogus", column="bogus_col")
    with pytest.raises(ValueError, match="unknown column"):
        load_metrics(path=target)


def test_unknown_name_raises_unknown_metric(tmp_path):
    """A known column under an unknown metric name raises ValueError
    containing `unknown metric`."""
    target = _tampered_copy(tmp_path, name="bogus_metric")
    with pytest.raises(ValueError, match="unknown metric"):
        load_metrics(path=target)


def test_compiler_parity_on_seeded_db(seeded_db):
    """Compiler parity on the session seed: gross_churned_arr equals the
    raw SUM(accounts.arr_cents) over window cancels in dollars, and
    active_accounts_eom equals the window kpis account count; the
    metric_value dispatch agrees and unknown names raise ValueError."""
    gross = queries.gross_churned_arr("2026-08-01", "2026-09-30")
    conn = db.connect(seeded_db, readonly=True)
    try:
        raw_cents = conn.execute(
            "SELECT COALESCE(SUM(a.arr_cents), 0) FROM subscription_events s "
            "JOIN accounts a ON a.id = s.account_id "
            "WHERE s.event_type = 'cancel' "
            "AND s.event_date BETWEEN '2026-08-01' AND '2026-09-30'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert gross == round(raw_cents / 100, 2)

    eom = queries.active_accounts_eom("2026-09-30")
    assert eom == queries.kpis("2026-09-01", "2026-09-30")["accounts"]

    assert (
        queries.metric_value("gross_churned_arr", "2026-08-01", "2026-09-30")
        == gross
    )
    assert queries.metric_value("active_accounts_eom", "2026-09-30") == eom
    with pytest.raises(ValueError):
        queries.metric_value("bogus", "2026-09-01", "2026-09-30")


def test_module_imports_both_styles():
    """Entry contract: semantics works both as `import agent.semantics`
    from the repo root and as `import semantics` from agent/, via the
    same dual import as queries.py."""
    package_style = subprocess.run(
        [sys.executable, "-c", "import agent.semantics"],
        cwd=AGENT_DIR.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert package_style.returncode == 0, package_style.stderr
    script_style = subprocess.run(
        [sys.executable, "-c", "import semantics"],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert script_style.returncode == 0, script_style.stderr


def test_summary_inlines_metric_defs():
    """compact_snapshot_summary appends the registry's name=definition
    pairs (per-decider grounding) while keeping the existing KPI output."""
    snapshot = {
        "window": {"range": "2026-07-01..2026-09-30"},
        "kpis": {
            "net_new_arr": 123456.0,
            "mrr": 9999.0,
            "nrr": 1.08,
            "accounts": 123,
        },
    }
    summary = questions.compact_snapshot_summary(snapshot)
    assert "$123,456" in summary
    assert "gross_churned_arr=" in summary
    assert "no active-base division" in summary

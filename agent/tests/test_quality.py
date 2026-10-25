"""DQ check tests: the planted Sept ingest bug must stay deterministic."""

from __future__ import annotations

import quality
import store


def test_checks_are_exactly_fail_fail_ok():
    result = quality.dq_checks(store.connect())
    by_name = {c["name"]: c for c in result["checks"]}
    # Probe contract: the card surfaces exactly these three checks.
    assert set(by_name) == set(quality.CHECK_NAMES)
    assert by_name["freshness"]["status"] == "ok"
    assert by_name["duplicates"]["status"] == "fail"
    assert by_name["nulls"]["status"] == "fail"


def test_null_ratio_breaches_threshold():
    result = quality.dq_checks(store.connect())
    assert result["null_ratio"] > 0.02  # 2% breach threshold
    nulls_check = next(c for c in result["checks"] if c["name"] == "nulls")
    assert nulls_check["score"] == round(result["null_ratio"], 4)


def test_duplicate_groups_detected():
    result = quality.dq_checks(store.connect())
    assert result["dup_groups"] > 0


def test_window_is_horizon_anchored_not_wall_clock():
    """Same read for default and explicit horizon — no dependence on today()."""
    a = quality.dq_checks(store.connect())
    b = quality.dq_checks(store.connect(), today=store.HORIZON)
    assert a["checks"] == b["checks"]
    assert a["window"] == b["window"]
    assert a["window"].endswith(store.HORIZON.isoformat())


def test_bug_window_inside_dq_window():
    """Invariant of the planted shape: the bug must always fall inside the DQ window."""
    start_s, end_s = quality.dq_window(store.HORIZON)
    start = store.date.fromisoformat(start_s)
    end = store.date.fromisoformat(end_s)
    assert start <= store.BUG_START and store.BUG_END <= end

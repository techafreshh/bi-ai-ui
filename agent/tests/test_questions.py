"""Contract tests for the decide node's NL domain logic (issue #10).

resolve_window must be deterministic — anchored on the data's own latest
event day, never a wall clock — because issue #10's acceptance pins the
exact ISO range the seeded Q3 query resolves to. build_question_set must
embed the user message verbatim in every prompt (transports key answers by
question id; per-query information can only enter through the prompt) and
stay inside the decide node's 7-question fan-out budget.
"""

import datetime as dt

import pytest

import questions


# --- resolve_window -----------------------------------------------------------


def test_q3_query_resolves_to_seeded_q3_window():
    """Acceptance anchor: 'Show me sales in Q3' against the seeded DB's
    horizon (data_end 2026-09-30) must resolve to exactly 2026-07-01
    ..2026-09-30 — the range issue #10's first acceptance clause pins."""
    assert questions.resolve_window(
        "Show me sales in Q3", data_end="2026-09-30"
    ) == ("2026-07-01", "2026-09-30")


def test_explicit_year_is_honored_even_outside_data():
    """An explicit year wins verbatim — even when the window lies outside
    the data range (components just render an empty series)."""
    assert questions.resolve_window(
        "Q3 2025 trends please", data_end="2026-09-30"
    ) == ("2025-07-01", "2025-09-30")
    assert questions.resolve_window(
        "sales in October 2026", data_end="2026-09-30"
    ) == ("2026-10-01", "2026-10-31")


def test_unpinned_month_backs_off_to_latest_complete():
    """A year-omitted month that is still incomplete relative to data_end
    resolves to its latest complete occurrence (one year back)."""
    assert questions.resolve_window(
        "December sales", data_end="2026-09-30"
    ) == ("2025-12-01", "2025-12-31")
    assert questions.resolve_window(
        "revenue in Sep", data_end="2026-09-30"
    ) == ("2026-09-01", "2026-09-30")


def test_no_period_falls_back_to_complete_quarter_of_data_end():
    """No quarter/month in the message: the complete quarter containing
    data_end — or the one before it when data_end sits mid-quarter."""
    assert questions.resolve_window(
        "Tell me something interesting", data_end="2026-09-30"
    ) == ("2026-07-01", "2026-09-30")
    assert questions.resolve_window(
        "hello?", data_end="2026-10-15"
    ) == ("2026-07-01", "2026-09-30")


def test_missing_data_end_still_resolves_a_quarter_window():
    """Degrade contract: data_end=None (unreadable DB) falls back to the
    wall-clock anchor — still a well-formed quarter-aligned window."""
    start, end = questions.resolve_window("anything", data_end=None)
    start_date, end_date = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    assert start_date < end_date
    assert start_date.day == 1
    assert end_date.day in (28, 29, 30, 31)


def test_previous_quarter_is_the_quarter_before_the_window():
    """read_state's comparison window: the quarter preceding the window's
    own quarter — independent of where inside the quarter the window sits."""
    assert questions.previous_quarter(("2026-07-01",)) == ("2026-04-01", "2026-06-30")
    assert questions.previous_quarter(("2026-07-31",)) == ("2026-04-01", "2026-06-30")
    assert questions.previous_quarter(("2026-01-01",)) == ("2025-10-01", "2025-12-31")


# --- build_question_set -------------------------------------------------------


def _sample_snapshot():
    return {
        "query": "Show me sales in Q3",
        "window": {"range": "2026-07-01..2026-09-30"},
        "kpis": {"net_new_arr": 123456.0, "mrr": 9999.0, "nrr": 1.08, "accounts": 123},
        "dq_checks": [{"name": "freshness", "status": "ok", "score": 0.99}],
        "churn": {"delta": 0.25, "baseline": 0.01, "current": 0.0125},
        "degraded": False,
    }


def test_question_set_shape_and_budget():
    """Registered set: exactly one choice (over the six catalog types) and
    two noul + one score, ids unique, comfortably inside the 7-question
    decide budget."""
    question_set = questions.build_question_set("ping", _sample_snapshot())
    assert 1 <= len(question_set) <= questions.MAX_DECISION_QUESTIONS
    assert questions.MAX_DECISION_QUESTIONS == 7
    by_id = {q.id: q for q in question_set}
    assert set(by_id) == {
        questions.CATALOG_QUESTION_ID,
        questions.REAL_CHANGE_QUESTION_ID,
        questions.ANOMALY_QUESTION_ID,
        questions.DQ_CONFIDENCE_QUESTION_ID,
    }
    choice = by_id[questions.CATALOG_QUESTION_ID]
    assert choice.type == "choice"
    assert tuple(choice.options) == questions.CATALOG_TYPES
    assert len(questions.CATALOG_TYPES) == 6
    assert by_id[questions.REAL_CHANGE_QUESTION_ID].type == "noul"
    assert by_id[questions.ANOMALY_QUESTION_ID].type == "noul"
    assert by_id[questions.DQ_CONFIDENCE_QUESTION_ID].type == "score"


def test_every_prompt_quotes_the_user_message_and_inlines_context():
    """Transports key answers by question id, so the user message and the
    KPI/DQ numbers must reach the decider through each prompt verbatim."""
    snapshot = _sample_snapshot()
    question_set = questions.build_question_set("What about <msg & \"quotes\">?", snapshot)
    for question in question_set:
        assert "What about <msg & \"quotes\">?" in question.prompt
        assert "2026-07-01..2026-09-30" in question.prompt
        assert "$123,456" in question.prompt
        assert "freshness=ok" in question.prompt


def test_prompt_summary_degrades_gracefully():
    """A degraded (empty) snapshot still yields a usable prompt summary —
    decide must never KeyError while building its questions."""
    question_set = questions.build_question_set("ping", {})
    for question in question_set:
        assert '"ping"' in question.prompt
        assert "no data snapshot available" in question.prompt

"""Decision-layer tests: routing table, deterministic policy, transport chain."""

from __future__ import annotations

import json
import os
from unittest import mock

import httpx
import pytest

import routes
from decision.client import (
    CHAT_FALLBACK_MODEL,
    DEFAULT_DECISION_MODEL,
    LOCAL_POLICY_MODEL,
    DecisionClient,
    _normalize_answers,
    local_decisions,
)
from graph import _gather_evidence

GOLDEN_QUERIES = {
    "Show me sales in Q3": "q3_sales",
    "Why did churn spike in August?": "churn_why",
    "Is the data clean?": "data_clean",
}
FOLLOW_UP_QUERIES = {
    "Investigate the churn cohort": "investigate_churn",
    "Report an issue with the chart": "report_issue",
    "Draw me a dragon": "off_catalog",
}


def transport_mock_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env["DECISION_TRANSPORT"] = "mock"
    return env


@pytest.mark.parametrize(("text", "route"), [*GOLDEN_QUERIES.items(), *FOLLOW_UP_QUERIES.items()])
def test_route_classification_table(text, route):
    assert routes.classify_route(text) == route


def test_local_policy_is_deterministic():
    with mock.patch.dict(os.environ, transport_mock_env(), clear=True):
        evidence = _gather_evidence()
        questions = routes.build_questions("churn_why")
        assert DecisionClient().run("churn_why", questions, evidence) == DecisionClient().run(
            "churn_why", questions, evidence
        )


def test_record_shape_complete():
    with mock.patch.dict(os.environ, transport_mock_env(), clear=True):
        decisions = DecisionClient().run("q3_sales", routes.build_questions("q3_sales"), _gather_evidence())
    assert len(decisions) == 5  # ≥4 per query is the §11 drawer gate
    for record in decisions:
        assert record["model"] == LOCAL_POLICY_MODEL
        assert isinstance(record["latency_ms"], (int, float)) and record["latency_ms"] >= 0
        assert record["threshold"] == pytest.approx(0.6)
        assert isinstance(record["passed"], bool)
        assert record["question"]
        assert record["id"] in {
            "component_choice", "real_vs_data_problem", "anomaly_callout",
            "dq_confidence", "granularity",
        }
        keys = set(record)
        assert keys == {
            "id", "type", "question", "options", "probabilities", "pick",
            "value", "threshold", "model", "latency_ms", "passed",
        }


def test_q3_component_choice_picks_kpi_row_and_line_chart_only():
    with mock.patch.dict(os.environ, transport_mock_env(), clear=True):
        decisions = DecisionClient().run("q3_sales", routes.build_questions("q3_sales"), _gather_evidence())
    choice = next(d for d in decisions if d["id"] == "component_choice")
    assert choice["pick"] == ["kpi_row", "line_chart"]
    assert choice["passed"] is True


def test_dq_path_noul_flags_data_problem():
    with mock.patch.dict(os.environ, transport_mock_env(), clear=True):
        decisions = DecisionClient().run("data_clean", routes.build_questions("data_clean"), _gather_evidence())
    noul = next(d for d in decisions if d["id"] == "real_vs_data_problem")
    # Issue gate: noul p>0.7 on the data-quality path.
    assert noul["probabilities"]["p"] > 0.7
    assert noul["pick"] == "data_problem"
    assert noul["passed"] is True
    # Evidence value is the measured null ratio, not invented.
    noul_value = noul["value"]
    assert isinstance(noul_value, float)
    assert noul_value == pytest.approx(_gather_evidence()["dq"]["null_ratio"], abs=1e-4)


def test_off_catalog_records_sub_threshold_choice():
    with mock.patch.dict(os.environ, transport_mock_env(), clear=True):
        decisions = DecisionClient().run("off_catalog", routes.build_questions("off_catalog"), _gather_evidence())
    choice = next(d for d in decisions if d["id"] == "component_choice")
    assert choice["pick"] == []
    assert choice["passed"] is False
    assert all(p < 0.6 for p in choice["probabilities"].values())


def test_mock_transport_env_forces_local_even_with_key():
    env = {**transport_mock_env(), "OPENROUTER_API_KEY": "sk-test"}
    with mock.patch.dict(os.environ, env, clear=True):
        decisions = DecisionClient().run("q3_sales", routes.build_questions("q3_sales"), _gather_evidence())
    assert {d["model"] for d in decisions} == {LOCAL_POLICY_MODEL}


def _answers_body(answers: list[dict]) -> dict:
    return {"answers": answers}


def test_live_chain_decisions_endpoint_success(monkeypatch):
    """Decisions-endpoint transport answers upstream; model proves arrival."""
    captured = {}

    def fake_post(api_key, path, payload, timeout):
        captured["path"] = path
        captured["payload"] = payload
        if path == "/decisions":
            return _answers_body([
                {"id": "component_choice", "probabilities": {"kpi_row": 0.99, "line_chart": 0.99}},
                {"id": "real_vs_data_problem", "probabilities": {"p": 0.2}},
            ])
        raise AssertionError("should not fall through")

    monkeypatch.setattr("decision.client._post_openrouter", fake_post)
    with mock.patch.dict(os.environ, {**transport_mock_env(), "DECISION_TRANSPORT": "live",
                                      "OPENROUTER_API_KEY": "sk-test"}, clear=True):
        decisions = DecisionClient().run("q3_sales", routes.build_questions("q3_sales"), _gather_evidence())
    assert captured["path"] == "/decisions"
    assert captured["payload"]["model"] == DEFAULT_DECISION_MODEL
    models = {d["model"] for d in decisions}
    assert models == {DEFAULT_DECISION_MODEL, LOCAL_POLICY_MODEL}  # gaps filled from policy
    choice = next(d for d in decisions if d["id"] == "component_choice")
    assert choice["pick"] == ["kpi_row", "line_chart"]


def test_live_chain_falls_back_to_chat_structured(monkeypatch):
    chat_json = json.dumps({"answers": [
        {"id": "component_choice", "probabilities": {"variance_callout": 0.95, "data_table": 0.95}},
    ]})

    def fake_post(api_key, path, payload, timeout):
        if path == "/decisions":
            raise httpx.HTTPStatusError("500", request=mock.Mock(), response=mock.Mock())
        assert path == "/chat/completions"
        assert payload["response_format"]["type"] == "json_schema"
        return {"choices": [{"message": {"content": chat_json}}]}

    monkeypatch.setattr("decision.client._post_openrouter", fake_post)
    with mock.patch.dict(os.environ, {**transport_mock_env(), "DECISION_TRANSPORT": "live",
                                      "OPENROUTER_API_KEY": "sk-test",
                                      "DECISION_MODEL": DEFAULT_DECISION_MODEL}, clear=True):
        decisions = DecisionClient().run("churn_why", routes.build_questions("churn_why"), _gather_evidence())
    chat_rows = [d for d in decisions if d["model"] == CHAT_FALLBACK_MODEL]
    assert [d["id"] for d in chat_rows] == ["component_choice"]
    assert chat_rows[0]["pick"] == ["variance_callout", "data_table"]  # catalog order


def test_live_chain_falls_back_to_local_policy(monkeypatch):
    def boom(api_key, path, payload, timeout):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr("decision.client._post_openrouter", boom)
    with mock.patch.dict(os.environ, {**transport_mock_env(), "DECISION_TRANSPORT": "live",
                                      "OPENROUTER_API_KEY": "sk-test"}, clear=True):
        decisions = DecisionClient().run("q3_sales", routes.build_questions("q3_sales"), _gather_evidence())
    assert {d["model"] for d in decisions} == {LOCAL_POLICY_MODEL}


def test_live_without_key_falls_back_to_local():
    with mock.patch.dict(os.environ, {**transport_mock_env(), "DECISION_TRANSPORT": "live"}, clear=True):
        decisions = DecisionClient().run("q3_sales", routes.build_questions("q3_sales"), _gather_evidence())
    assert {d["model"] for d in decisions} == {LOCAL_POLICY_MODEL}


def test_normalize_answers_never_returns_fewer_than_questions():
    """Any unparseable upstream answer degrades per-question, not per-run."""
    questions = routes.build_questions("data_clean")
    raw = [
        {"id": "component_choice", "probabilities": {"data_quality_card": 1.5}},  # clamped
        {"id": "ghost", "probabilities": {"p": 0.5}},  # unknown id skipped
        {"id": "dq_confidence"},  # no probabilities -> skipped
    ]
    records = _normalize_answers(raw, questions, 0.6, "test-model", 40.0, {}, "data_clean")
    assert [d["id"] for d in records] == [q.id for q in questions]
    choice = next(d for d in records if d["id"] == "component_choice")
    assert choice["model"] == "test-model"
    assert choice["probabilities"]["data_quality_card"] == 1.0  # clamped to [0, 1]
    filled = next(d for d in records if d["id"] == "dq_confidence")
    assert filled["model"] == LOCAL_POLICY_MODEL


def test_per_route_policy_tables_are_complete():
    """Every route answers all five questions from the static table alone."""
    for route in routes.ROUTES:
        evidence = {"dq": {"null_ratio": 0.03, "dup_groups": 5},
                    "var": {"delta": 0.5}, "q3": {"net_new_arr_q3": 1000}}
        records = local_decisions(route, routes.build_questions(route), evidence, 0.6)
        assert len(records) == 5

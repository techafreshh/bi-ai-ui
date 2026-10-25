"""Graph-level tests: each §11 golden path produces its validated layout.

These invoke the full five-node graph directly (``ainvoke``) — no OpenRouter
access (DECISION_TRANSPORT=mock; no key in the environment), and with the
deterministic seeded store from conftest.
"""

from __future__ import annotations

import asyncio
import os
from unittest import mock

from langchain_core.messages import AIMessageChunk, HumanMessage

import graph

Q3_QUERY = "Show me sales in Q3"
CHURN_QUERY = "Why did churn spike in August?"
DQ_QUERY = "Is the data clean?"
INVESTIGATE_QUERY = "Investigate the churn cohort"
REPORT_QUERY = "Report an issue with the chart"
OFF_CATALOG_QUERY = "Draw me a dragon"


def run_graph(text: str, env_overrides: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env["DECISION_TRANSPORT"] = "mock"
    env.update(env_overrides or {})
    graph.reset_text_model()
    with mock.patch.dict(os.environ, env, clear=True):
        result = asyncio.run(
            graph.build_graph().ainvoke(
                {"messages": [HumanMessage(content=text)]},
                config={"configurable": {"thread_id": f"test-{abs(hash(text)) % 10**8}"}},
            )
        )
    graph.reset_text_model()
    return result


def item_types(result: dict) -> list[str]:
    return [item["type"] for item in result["a2ui"]["items"]]


def decision(result: dict, question_id: str) -> dict:
    return next(d for d in result["decisions"] if d["id"] == question_id)


def test_graph_state_contract_keys():
    """The wire state exposes exactly a2ui/decisions/status over messages."""
    keys = set(graph.AgentState.__annotations__)
    assert {"a2ui", "decisions", "status"} <= keys


def test_q3_path_kpis_and_line_chart_with_q3_range():
    result = run_graph(Q3_QUERY)
    assert result["status"]["stage"] == "done"
    assert result["status"]["fallback"] is False
    assert item_types(result) == ["kpi_row", "line_chart"]  # insight_text dropped <0.6
    kpis = result["a2ui"]["items"][0]["props"]["kpis"]
    assert 3 <= len(kpis) <= 4
    positive = sum(1 for k in kpis if isinstance(k["delta"], (int, float)) and k["delta"] > 0)
    assert positive >= 3
    chart = result["a2ui"]["items"][1]["props"]
    assert chart["range"] == "2026-07-01..2026-09-30"
    assert len(chart["points"]) == 92
    # Decisions: ≥4 per query, drawer-complete with model and latency.
    assert len(result["decisions"]) >= 4
    for d in result["decisions"]:
        assert d["model"] and isinstance(d["latency_ms"], (int, float))
    # The reply lists the assembled components (chat mini-card, lightweight).
    assert "KPI row" in result["messages"][-1].content
    assert "Line chart" in result["messages"][-1].content


def test_churn_path_callout_and_cohort_table():
    result = run_graph(CHURN_QUERY)
    types = item_types(result)
    assert "variance_callout" in types and "data_table" in types
    callout = next(i for i in result["a2ui"]["items"] if i["type"] == "variance_callout")["props"]
    assert callout["delta"] > 0.2
    assert "pricing change" in callout["cause"].lower()
    assert callout["cta"] == "investigate_churn"
    table = next(i for i in result["a2ui"]["items"] if i["type"] == "data_table")["props"]
    assert 38 <= len(table["rows"]) <= 50
    assert set(table["columns"]) == {"account", "segment", "mrr", "tenure_months", "plan"}
    noul = decision(result, "real_vs_data_problem")
    assert noul["pick"] == "real_change"  # the spike is real business change


def test_dq_path_checks_and_warning_banner():
    result = run_graph(DQ_QUERY)
    assert item_types(result)[0] == "data_quality_card"
    card = result["a2ui"]["items"][0]["props"]
    by_name = {c["name"]: c["status"] for c in card["checks"]}
    assert by_name == {"freshness": "ok", "duplicates": "fail", "nulls": "fail"}
    noul = decision(result, "real_vs_data_problem")
    assert noul["probabilities"]["p"] > 0.7
    # Warning banner condition (noul p>0.7) surfaces in status for the canvas.
    assert result["status"]["warnings"]
    assert "Data problem likely" in result["status"]["warnings"][0]
    score = decision(result, "dq_confidence")
    assert score["type"] == "score" and score["value"] is not None


def test_investigate_drills_into_cohort():
    result = run_graph(INVESTIGATE_QUERY)
    types = item_types(result)
    assert "data_table" in types and "kpi_row" in types
    table = next(i for i in result["a2ui"]["items"] if i["type"] == "data_table")["props"]
    assert 38 <= len(table["rows"]) <= 50
    kpis = next(i for i in result["a2ui"]["items"] if i["type"] == "kpi_row")["props"]["kpis"]
    labels = [k["label"] for k in kpis]
    assert any("Churned accounts" in label for label in labels)


def test_report_issue_answers_from_catalog():
    result = run_graph(REPORT_QUERY)
    assert set(item_types(result)) == {"insight_text"}
    assert result["status"]["fallback"] is False
    assert result["status"]["route"] == "report_issue"


def test_every_golden_query_has_at_least_four_drawer_decisions():
    for text in (Q3_QUERY, CHURN_QUERY, DQ_QUERY):
        result = run_graph(text)
        assert len(result["decisions"]) >= 4
        assert all(d["model"] == "demo-local-policy" for d in result["decisions"])


def test_off_catalog_falls_back_to_single_insight():
    result = run_graph(OFF_CATALOG_QUERY)
    assert item_types(result) == ["insight_text"]
    assert result["status"]["fallback"] is True
    assert result["status"]["route"] == "off_catalog"
    choice = decision(result, "component_choice")
    assert choice["passed"] is False and choice["pick"] == []
    # Unknown markup is never rendered: only catalog members appear.
    assert set(item_types(result)) <= set(
        ("kpi_row", "line_chart", "variance_callout", "data_table",
         "data_quality_card", "insight_text"))


def test_fill_without_key_returns_warning_prefixed_reply():
    result = run_graph(Q3_QUERY)  # env patch strips OPENROUTER_API_KEY
    reply = result["messages"][-1]
    # Walking-skeleton no-crash contract: canned copy carries the warning marker.
    assert reply.content.startswith("⚠")
    assert "OPENROUTER_API_KEY" in reply.content


def test_canned_narrative_lands_in_layout_when_insight_text_picked():
    result = run_graph(INVESTIGATE_QUERY)  # insight_text p=0.66 ≥ threshold
    assert "insight_text" in item_types(result)
    body = result["a2ui"]["slotCopy"].get("insight_text.body", "")
    assert body and "Churned MRR" in body


def test_narrative_streams_via_text_model_when_key_present():
    class FakeModel:
        async def astream(self, _messages):
            yield AIMessageChunk(content="Q3 accelerated ")
            yield AIMessageChunk(content="on the campaign.")

    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env.update({"DECISION_TRANSPORT": "mock", "OPENROUTER_API_KEY": "sk-test"})
    graph.reset_text_model()
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(graph, "get_text_model", return_value=FakeModel()):
            result = asyncio.run(
                graph.build_graph().ainvoke(
                    {"messages": [HumanMessage(content=INVESTIGATE_QUERY)]},
                    config={"configurable": {"thread_id": "test-live-narrative"}},
                )
            )
    graph.reset_text_model()
    assert result["a2ui"]["slotCopy"]["insight_text.body"] == "Q3 accelerated on the campaign."
    reply = result["messages"][-1]
    assert "Q3 accelerated on the campaign." in reply.content
    assert not reply.content.startswith("⚠")


def test_each_node_emits_the_full_state_triple(monkeypatch):
    """Wire contract: every stage snapshots a2ui+decisions+status together."""
    emitted: list[dict] = []

    async def emit_spy(config, a2ui, decisions, status):
        emitted.append({"stage": status.get("stage"), "route": status.get("route"),
                        "a2ui": a2ui, "decisions": decisions, "status": status})

    monkeypatch.setattr(graph, "_emit", emit_spy)
    run_graph(DQ_QUERY)
    stages = [e["stage"] for e in emitted]
    assert stages == ["reading", "deciding", "layout", "querying", "done"]
    for emit in emitted:
        assert set(emit) == {"stage", "route", "a2ui", "decisions", "status"}
    # Decide carries the drawer data; layout the skeleton; done the fallback flag.
    assert emitted[1]["decisions"]
    assert all(d["id"] for d in emitted[1]["decisions"])
    assert emitted[2]["a2ui"]["items"]
    assert emitted[4]["status"]["stage"] == "done"
    assert "fallback" in emitted[4]["status"]


def test_layout_skeleton_carries_placeholder_props(monkeypatch):
    skeleton: dict | None = None

    async def emit_spy(config, a2ui, decisions, status):
        nonlocal skeleton
        if status.get("stage") == "layout":
            skeleton = a2ui

    monkeypatch.setattr(graph, "_emit", emit_spy)
    run_graph(Q3_QUERY)
    assert skeleton is not None
    assert [i["type"] for i in skeleton["items"]] == ["kpi_row", "line_chart"]
    assert skeleton["items"][0]["props"] == {"kpis": []}  # loading placeholder

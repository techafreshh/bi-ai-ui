"""Black-box acceptance tests for the five-node agent graph (issue #10).

Drives the same code path the browser hits — RunAgentInput →
LangGraphAGUIAgent → ag-ui EventEncoder SSE — over the real graph with the
heuristics standing in for both models (no OpenRouter key needed), and
parses every STATE_SNAPSHOT off the wire. Each test names the acceptance
clause it proves; the seeded DB comes from the generator's real entrypoint
as the suite-wide shared seed (one subprocess run per pytest session,
provided by tests/conftest.py with a module-scoped DEFAULT_DB_PATH
swap-restore per consumer).
"""

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from ag_ui.core.types import RunAgentInput, UserMessage
from ag_ui.encoder import EventEncoder
from copilotkit import LangGraphAGUIAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

import catalog
import db
import graph as agent_graph
from decision.client import DecisionClient, MockTransport

Q3_RANGE = "2026-07-01..2026-09-30"


def _heuristic_client():
    """The deterministic decider the graph falls back to — pinned here so
    the tests need no OpenRouter key and no live endpoint."""
    return DecisionClient(agent_graph.HeuristicTransport())


def _run_sse(monkeypatch, message="Show me sales in Q3", chat_model=None,
             decision_client=None, thread_id=None):
    """Run the full graph once through the real AG-UI adapter and return
    (raw SSE text, snapshots). Every snapshot's full state dict is parsed
    from the wire — nothing is poked behind the adapter.

    decision_client: decider factory to monkeypatch in; None (the default)
    pins the deterministic heuristic so no key or live endpoint is needed.
    Tests that need a specific decider (the all-sub-threshold mock) pass
    their own — passed factories are never overwritten by the heuristic.
    thread_id: the AG-UI thread the run joins; None (the default) starts a
    fresh one. The Investigate drill-down (issue #16) reuses ONE thread
    across two runs — the CTA posts the follow-up into the same chat."""
    if decision_client is not None:
        monkeypatch.setattr(agent_graph, "get_decision_client", decision_client)
    else:
        monkeypatch.setattr(agent_graph, "get_decision_client", _heuristic_client)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    if chat_model is not None:
        monkeypatch.setattr(agent_graph, "build_chat_model", chat_model)

    agent = LangGraphAGUIAgent(
        name="bi_agent", description="graph contract test", graph=agent_graph.graph
    )
    run_input = RunAgentInput(
        thread_id=thread_id or f"test-{uuid.uuid4()}",
        run_id=f"run-{uuid.uuid4()}",
        state={},
        messages=[UserMessage(id="m1", role="user", content=message)],
        tools=[],
        context=[],
        forwarded_props={},
    )
    encoder = EventEncoder(accept="text/event-stream")

    async def collect():
        return "".join(
            [encoder.encode(event) async for event in agent.run(run_input)]
        )

    raw = asyncio.run(collect())
    snapshots = [
        json.loads(line.removeprefix("data: "))
        for line in raw.splitlines()
        if line.startswith("data: ") and "STATE_SNAPSHOT" in line
    ]
    return raw, snapshots


def _final_state(snapshots):
    """The last STATE_SNAPSHOT's state — the graph's end state on the wire."""
    return snapshots[-1]["snapshot"]


def _emitted_a2ui(snapshots):
    """Every a2ui dict that was ever streamed, in order."""
    return [
        state["snapshot"]["a2ui"]
        for state in snapshots
        if state["snapshot"].get("a2ui") is not None
    ]


# --- Acceptance clause 1: Q3 query streams a windowed kpi_row + line_chart ----


def test_q3_query_streams_kpi_row_and_line_chart(seeded_db, monkeypatch):
    """'Show me sales in Q3' streams state whose final a2ui carries at least
    one kpi_row whose tiles embed the Q3-2026 window
    (2026-07-01..2026-09-30) and a line_chart with range exactly
    '2026-07-01..2026-09-30'; the run finishes cleanly."""
    raw, snapshots = _run_sse(monkeypatch)

    assert "RUN_FINISHED" in raw
    final = _final_state(snapshots)
    items = final["a2ui"]["items"]

    kpi_rows = [item for item in items if item["type"] == "kpi_row"]
    assert len(kpi_rows) >= 1, items
    for tile in kpi_rows[0]["props"]["kpis"]:
        assert Q3_RANGE in tile["label"], tile

    charts = [item for item in items if item["type"] == "line_chart"]
    assert len(charts) >= 1, items
    assert charts[0]["props"]["range"] == Q3_RANGE

    # The window is also visible on the streamed shared state, not just in
    # the component props.
    assert final["snapshot"]["window"]["range"] == Q3_RANGE


# --- Issue #16 golden path B: churn spike drill-down and Investigate CTA ----- 


def test_golden_churn_streams_callout_cause_and_cohort(seeded_db, monkeypatch):
    """Clause 1: 'Why did churn spike in August?' streams a final a2ui whose
    variance_callout carries delta > 0.2, a cause naming the planted pricing
    change, and the issue's machine CTA id; its data_table holds 38..50 rows,
    >= 80% of them Growth-segment, with row-key columns. Repeated runs
    reproduce the same callout delta and 40-row cohort — deterministic SQL,
    no model in the loop."""
    first = None
    for _ in range(3):
        _, snapshots = _run_sse(monkeypatch, message="Why did churn spike in August?")
        final = _final_state(snapshots)
        items = final["a2ui"]["items"]
        callouts = [i for i in items if i["type"] == "variance_callout"]
        tables = [i for i in items if i["type"] == "data_table"]
        assert callouts, items
        props = callouts[0]["props"]
        assert props["delta"] > 0.2, props
        assert "pricing" in props["cause"], props
        assert props["cta"] == agent_graph.INVESTIGATE_CTA, props
        assert tables, items
        table_props = tables[0]["props"]
        assert 38 <= len(table_props["rows"]) <= 50, len(table_props["rows"])
        growth_share = sum(
            row["segment"] == "Growth" for row in table_props["rows"]
        ) / len(table_props["rows"])
        assert growth_share >= 0.8, growth_share
        row_keys = set(table_props["rows"][0])
        assert {"segment", "mrr", "tenure_months", "plan"} <= row_keys
        assert set(table_props["columns"]) == row_keys
        if first is None:
            first = (props["delta"], len(table_props["rows"]))
        else:
            assert (props["delta"], len(table_props["rows"])) == first
    assert first[0] == 1.0 and first[1] == 40  # the seed's pinned spike


def test_golden_churn_noul_decision_is_the_data_problem_probability(
    seeded_db, monkeypatch
):
    """Clause 3 (issue #16; shape as flattened by #17): the churn query's
    decisions[] carries exactly one data_problem noul whose probabilities
    is the scalar p(movement is a data problem) in [0, 1] — under the
    heuristic the churn route reads 0.06, so the scalar stays below the
    0.5 pick line and the banner gate alike."""
    _, snapshots = _run_sse(monkeypatch, message="Why did churn spike in August?")
    decisions = _final_state(snapshots)["decisions"]
    matches = [
        d for d in decisions
        if d["id"] == agent_graph.questions.DATA_PROBLEM_QUESTION_ID
    ]
    assert len(matches) == 1, decisions
    decision = matches[0]
    assert decision["type"] == "noul", decision
    probability = decision["probabilities"]
    assert isinstance(probability, float) and not isinstance(probability, bool)
    assert 0.0 <= probability <= 1.0, decision
    assert decision["pick"] in {"yes", "no"}, decision
    assert probability < 0.5 and decision["pick"] == "no", decision


def test_investigate_followup_runs_cohort_drilldown(seeded_db, monkeypatch):
    """Clause 2: after the churn question completes, a follow-up message
    framed with the fixed Investigate prefix — the exact payload the CTA
    sends (web/src/hooks/useInvestigate.tsx) — starts a second run on the
    SAME thread whose refreshed a2ui references the cohort: a data_table
    with a segment filter (all rows Growth, cancel dates present) and an
    insight_text titled '... churn cohort ...' for that cohort."""
    thread_id = f"test-{uuid.uuid4()}"
    _, first_snapshots = _run_sse(
        monkeypatch,
        message="Why did churn spike in August?",
        thread_id=thread_id,
    )
    first = _final_state(first_snapshots)
    assert not first["snapshot"]["investigate"]

    _, snapshots = _run_sse(
        monkeypatch,
        message=(
            f"{agent_graph.INVESTIGATE_PREFIX} — Why did churn spike in August?"
        ),
        thread_id=thread_id,
    )
    final = _final_state(snapshots)
    # The follow-up re-pins the same window (the framing carries the question).
    assert final["snapshot"]["window"]["range"] == "2026-08-01..2026-08-31"
    items = final["a2ui"]["items"]
    tables = [i for i in items if i["type"] == "data_table"]
    insights = [i for i in items if i["type"] == "insight_text"]
    assert tables and insights, items
    table_props = tables[0]["props"]
    assert table_props["filters"] == ["segment"], table_props
    assert len(table_props["rows"]) >= 1, table_props
    for row in table_props["rows"]:
        assert row["segment"] == "Growth", row
        assert "cancelled_on" in row, row
    assert "churn cohort" in insights[0]["props"]["title"], insights[0]["props"]


# --- Acceptance clause 2: decisions[] shape -----------------------------------


def test_decisions_stream_shape(seeded_db, monkeypatch):
    """The streamed decisions array has between 1 and 7 entries and every
    entry carries id, type, probabilities, pick, model and latency_ms (the
    latency a number >= 0, the pick a real value, not a placeholder)."""
    _, snapshots = _run_sse(monkeypatch)
    decisions = _final_state(snapshots)["decisions"]

    assert 1 <= len(decisions) <= 7
    seen_kinds = set()
    for decision in decisions:
        for key in ("id", "type", "probabilities", "pick", "model", "latency_ms"):
            assert key in decision, decision
        assert isinstance(decision["latency_ms"], (int, float))
        assert decision["latency_ms"] >= 0
        assert isinstance(decision["model"], str) and decision["model"]
        assert decision["pick"] is not None
        seen_kinds.add(decision["type"])
    assert seen_kinds == {"choice", "noul", "score"}


# --- Acceptance clause 3: all-sub-threshold picks fall back --------------------


def test_all_subthreshold_picks_fall_back_to_default_insight(seeded_db, monkeypatch):
    """A decider scoring every component below threshold empties the canvas:
    the streamed a2ui is exactly the catalog's single-insight_text fallback
    layout, and nothing else leaks through."""
    question_set = agent_graph.questions.build_question_set("Show me sales in Q3", {})
    answers = {
        question.id: {
            "id": question.id,
            "type": question.type,
            "probabilities": (
                {option: 0.1 for option in question.options}
                if question.type == "choice"
                else 0.1
            ),
        }
        for question in question_set
    }

    def subthreshold_client():
        return DecisionClient(MockTransport(answers=answers))

    _, snapshots = _run_sse(monkeypatch, decision_client=subthreshold_client)

    final_a2ui = _final_state(snapshots)["a2ui"]
    assert [item["type"] for item in final_a2ui["items"]] == ["insight_text"]
    # The layout shape equals the catalog fallback exactly — fill's copy in
    # slotCopy is the only allowed delta.
    fallback = catalog.fallback_layout().model_dump()
    assert final_a2ui["items"] == fallback["items"]
    assert final_a2ui["v"] == fallback["v"]
    assert final_a2ui["layout"] == fallback["layout"]


# --- Acceptance clause 4: every emitted a2ui validates ------------------------


def test_every_streamed_a2ui_passes_layout_validation(seeded_db, monkeypatch):
    """Run the graph on the seeded DB (down-stream a2ui from every node)
    AND on a degraded snapshot: every intervening a2ui ever streamed —
    from any node — parses through A2UILayout."""
    _, snapshots = _run_sse(monkeypatch)
    emitted = _emitted_a2ui(snapshots)
    assert emitted, "expected at least one a2ui on the wire"
    for a2ui in emitted:
        catalog.A2UILayout.model_validate(a2ui)  # raises on any violation

    # Degraded state: the layout node must emit a validating fallback even
    # when the decider endorses components it cannot build data for.
    with_db = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = Path("/nonexistent/data/app.db")
    try:
        _, degraded_snapshots = _run_sse(
            monkeypatch, message="Show me churn and sales for Q3"
        )
    finally:
        db.DEFAULT_DB_PATH = with_db
    for a2ui in _emitted_a2ui(degraded_snapshots):
        catalog.A2UILayout.model_validate(a2ui)


def test_layout_node_survives_a_corrupted_snapshot():
    """The parse assertion is in the node itself: feed the layout node a
    snapshot full of garbage (non-numeric KPI values, a probability out of
    [0, 1] on the choice answer) and it still stores a validating layout —
    the fallback, never a raise."""
    corrupt_state = {
        "messages": [],
        "snapshot": {
            "query": "garbage in",
            "window": {"start": "not-a-date", "end": None, "range": None},
            "kpis": {"net_new_arr": "-million", "accounts": [1, 2]},
        },
        "decisions": [
            {
                "id": agent_graph.questions.CATALOG_QUESTION_ID,
                "type": "choice",
                "probabilities": {"kpi_row": 1.5, "line_chart": "high"},
            }
        ],
    }
    result = asyncio.run(agent_graph.layout(corrupt_state))
    catalog.A2UILayout.model_validate(result["a2ui"])
    assert result["status"] == agent_graph.STATUSES["layout"]


# --- Acceptance clause 5: fill makes exactly one text-model call --------------


def test_fill_makes_exactly_one_model_call(seeded_db, monkeypatch):
    """The fill node triggers exactly ONE streamed model call (counting
    wrapper), writes the reply into slotCopy.summary, and the final
    streamed AIMessage equals the fake's text."""
    reply = "Q3 net-new ARR accelerated on the pricing ramp."
    base_model = FakeListChatModel(responses=[reply])

    class CountingModel:
        """Counts astream invocations around the fake — the mock counter."""

        def __init__(self):
            self.calls = 0

        async def astream(self, messages):
            self.calls += 1
            async for chunk in base_model.astream(messages):
                yield chunk

    counter = CountingModel()
    _, snapshots = _run_sse(monkeypatch, chat_model=lambda: counter)

    assert counter.calls == 1, "fill must make exactly one text-model call"
    final = _final_state(snapshots)
    final_a2ui = final["a2ui"]
    catalog.A2UILayout.model_validate(final_a2ui)
    assert final_a2ui["slotCopy"]["summary"] == reply

    messages = final["messages"]
    # Messages serialize langchain-style: the closing message IS the
    # assistant's streamed summary.
    assert messages[-1]["type"] == "ai"
    assert messages[-1]["content"] == reply
    assert [m["type"] for m in messages] == ["human", "ai"]


def test_fill_routes_parsed_copy_into_slots(seeded_db, monkeypatch):
    """A model that answers with the requested JSON has its string values
    routed into slotCopy (kpis.title, insight_text.body), and the summary
    key closes the chat."""
    body = "Net-new ARR grew through Q3 on the August campaign."
    model_text = json.dumps(
        {"kpis.title": "Q3 ARR surge", "insight_text.body": body, "summary": "done"}
    )
    _, snapshots = _run_sse(
        monkeypatch, chat_model=lambda: FakeListChatModel(responses=[model_text])
    )
    slot_copy = _final_state(snapshots)["a2ui"]["slotCopy"]
    assert slot_copy["kpis.title"] == "Q3 ARR surge"
    assert slot_copy["insight_text.body"] == body
    final_messages = _final_state(snapshots)["messages"]
    assert final_messages[-1]["type"] == "ai"
    assert final_messages[-1]["content"] == "done"


# --- Issue #17: golden path C — data-quality surfacing --------------------


def test_data_problem_probability_extraction():
    """The tolerant extraction mirrors _catalog_probabilities: the
    data_problem noul's scalar is clamped into [0, 1]; anything else
    (missing list, missing id, malformed value, bool) reads 0.0 so the
    banner gate stays shut instead of raising."""
    question_id = agent_graph.questions.DATA_PROBLEM_QUESTION_ID
    decisions = [
        {
            "id": agent_graph.questions.CATALOG_QUESTION_ID,
            "type": "choice",
            "probabilities": {"data_quality_card": 0.95},
        },
        {"id": question_id, "type": "noul", "probabilities": 0.94},
    ]
    assert agent_graph._data_problem_probability(decisions) == 0.94
    assert agent_graph._data_problem_probability(decisions[:1]) == 0.0
    assert agent_graph._data_problem_probability(None) == 0.0
    assert agent_graph._data_problem_probability([]) == 0.0
    assert agent_graph._data_problem_probability(
        [{"id": question_id, "probabilities": "high"}]
    ) == 0.0
    assert agent_graph._data_problem_probability(
        [{"id": question_id, "probabilities": True}]
    ) == 0.0
    # Clamping: a misbehaving transport can never push the gate past [0, 1].
    assert agent_graph._data_problem_probability(
        [{"id": question_id, "probabilities": 1.5}]
    ) == 1.0
    assert agent_graph._data_problem_probability(
        [{"id": question_id, "probabilities": -0.4}]
    ) == 0.0


def _dq_card_layout(props):
    """One data_quality_card layout through the real schema — _dq_slot_copy
    reads exactly what A2UILayout validates."""
    return catalog.A2UILayout.model_validate(
        {"items": [{"type": "data_quality_card", "props": props}]}
    ).model_dump()


def test_dq_slot_copy_names_failing_checks_and_detail():
    """Clause 4's writer: the deterministic summary names the failing
    checks verbatim (carrying the literal 'duplicate'/'null' substrings)
    and appends the narrated detail (carrying 'Sep' and 'Startup')."""
    detail = (
        "duplicate and null usage rows cluster in Sep 8-21, 2026 "
        "(2026-09-08..2026-09-21), Startup segment"
    )
    copy = agent_graph._dq_slot_copy(
        _dq_card_layout(
            {
                "checks": [
                    {"name": "freshness", "status": "ok", "score": 1.0},
                    {"name": "duplicates", "status": "fail", "score": 0.87},
                    {"name": "nulls", "status": "fail", "score": 0.95},
                ],
                "data_problem": 0.94,
                "data_problem_detail": detail,
            }
        )
    )
    assert copy.startswith(
        "Data-quality checks failing: duplicates, nulls — "
    ), copy
    assert detail in copy


def test_dq_slot_copy_all_passing_and_unread_variants():
    """The other two deterministic variants: checks that all pass read the
    all-passing line (detail still appended when present); empty/missing
    checks (degraded snapshot) read the could-not-read line without a
    detail; no DQ card in the layout reads None so fill writes no orphan
    key."""
    detail = "duplicate and null usage rows cluster in Sep 8-14, 2026"
    all_pass = agent_graph._dq_slot_copy(
        _dq_card_layout(
            {
                "checks": [
                    {"name": "freshness", "status": "ok", "score": 1.0},
                    {"name": "duplicates", "status": "ok", "score": 1.0},
                    {"name": "nulls", "status": "ok", "score": 0.99},
                ],
                "data_problem_detail": detail,
            }
        )
    )
    assert all_pass.startswith(
        "All data-quality checks pass for this window. — "
    ), all_pass
    assert detail in all_pass

    unread = agent_graph._dq_slot_copy(_dq_card_layout({"checks": []}))
    assert unread == "Data-quality checks could not be read for this window."

    # A MISSING checks key (only reachable through a raw dict — the schema
    # requires the field) degrades to the same could-not-read variant.
    assert agent_graph._dq_slot_copy(
        {"items": [{"type": "data_quality_card", "props": {}}]}
    ) == "Data-quality checks could not be read for this window."

    no_card = catalog.fallback_layout().model_dump()
    assert agent_graph._dq_slot_copy(no_card) is None


def test_dq_problem_detail_humanizes_the_scope():
    """The banner's evidence line: narrated window ('Sep 8-21, 2026' with
    the ASCII hyphen, ISO range in parens) plus the segment — malformed
    scopes total to None instead of breaking the card refresh."""
    assert agent_graph._dq_problem_detail(
        {"start": "2026-09-08", "end": "2026-09-21", "segment": "Startup"}
    ) == (
        "duplicate and null usage rows cluster in Sep 8-21, 2026 "
        "(2026-09-08..2026-09-21), Startup segment"
    )
    assert agent_graph._dq_problem_detail(
        {"start": "2026-09-28", "end": "2026-10-02", "segment": "Startup"}
    ) == (
        "duplicate and null usage rows cluster in Sep 28-Oct 2, 2026 "
        "(2026-09-28..2026-10-02), Startup segment"
    )
    assert agent_graph._dq_problem_detail({"start": "garbage", "end": "x"}) is None
    assert agent_graph._dq_problem_detail(None) is None
    with pytest.raises(ValueError):
        agent_graph._humanize_day_range("2026-09-21", "2026-09-08")


def test_read_state_stores_dq_scope_and_scopes_dq_checks(seeded_db):
    """Clause 1's context: read_state detects the anomaly window (on the
    seeded DB exactly the Sept 8-21 Startup plant) and scopes the decision
    context's dq_checks to it — duplicates/nulls fail right there, without
    waiting for the query node."""
    result = asyncio.run(
        agent_graph.read_state(
            {"messages": [HumanMessage(content="Is the data clean?")]}
        )
    )
    snapshot = result["snapshot"]
    assert snapshot["dq_scope"] == {
        "start": "2026-09-08",
        "end": "2026-09-21",
        "segment": "Startup",
    }
    statuses = {
        check["name"]: check["status"] for check in snapshot["dq_checks"]
    }
    assert statuses == {"freshness": "ok", "duplicates": "fail", "nulls": "fail"}


def test_read_state_dq_scope_is_none_on_a_missing_db(tmp_path):
    """Degrade contract: an unreadable DB leaves dq_scope None (and the
    snapshot degraded) instead of raising — the anomaly detection never
    breaks the run."""
    original = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = tmp_path / "missing.db"
    try:
        result = asyncio.run(
            agent_graph.read_state(
                {"messages": [HumanMessage(content="Is the data clean?")]}
            )
        )
    finally:
        db.DEFAULT_DB_PATH = original
    assert result["snapshot"]["degraded"] is True
    assert result["snapshot"]["dq_scope"] is None


# --- Resilience: no DB, no key -------------------------------------------------


def test_graph_completes_without_db_or_key(monkeypatch, tmp_path):
    """Degrade contract: with the DB pointed at a nonexistent path and no
    OPENROUTER_API_KEY, the graph still completes — degraded snapshot, 1..7
    decisions, a validating a2ui, status 'done' — and never raises out of
    the run. This replaces the deleted single-node chat test in
    test_server.py: fill's missing-key failure degrades to deterministic
    copy instead of failing the run."""
    original_db_path = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = tmp_path / "missing.db"
    try:
        raw, snapshots = _run_sse(monkeypatch)
    finally:
        db.DEFAULT_DB_PATH = original_db_path

    assert "RUN_FINISHED" in raw
    final = _final_state(snapshots)
    assert final["status"] == agent_graph.STATUSES["fill"] == "done"
    assert final["snapshot"]["degraded"] is True
    catalog.A2UILayout.model_validate(final["a2ui"])
    assert 1 <= len(final["decisions"]) <= 7


def test_status_advances_through_the_five_nodes(seeded_db, monkeypatch):
    """The streamed `status` walks the literal node sequence:
    reading_state → deciding → building_layout → querying → done. (The
    adapter emits one extra final snapshot repeating the end state, so
    consecutive duplicates collapse.)"""
    _, snapshots = _run_sse(monkeypatch)
    statuses = [
        state["snapshot"]["status"]
        for state in snapshots
        if state["snapshot"].get("status")
    ]
    consecutive = [
        status for index, status in enumerate(statuses)
        if index == 0 or status != statuses[index - 1]
    ]
    assert consecutive == [
        "reading_state",
        "deciding",
        "building_layout",
        "querying",
        "done",
    ]


# --- Issue #19: env-configurable models and threshold -------------------------


def _stub_batch_client_factory():
    """A decider factory over a BATCH transport answering every question
    with a flat 0.1 raw payload. Batch (not the heuristic's per-question
    seam) so no transport-declared model steps in front of the env-derived
    label, and 0.1 so the layout cut empties deterministically — decisions
    still stream, one per question."""
    question_set = agent_graph.questions.build_question_set("Show me sales in Q3", {})
    answers = {
        question.id: {
            "id": question.id,
            "type": question.type,
            "probabilities": (
                {option: 0.1 for option in question.options}
                if question.type == "choice"
                else 0.1
            ),
        }
        for question in question_set
    }

    class StubBatchTransport:
        async def ask_batch(self, questions):
            return answers

    return lambda: DecisionClient(StubBatchTransport())


def test_per_type_model_overrides_stream_per_answer(seeded_db, monkeypatch):
    """Issue #19: with DECISION_MODEL_CHOICE/_NOUL/_SCORE set, each streamed
    decisions[].model names the override its answer's type routed to —
    verbatim on the wire, the exact string the drawer renders."""
    monkeypatch.setenv("DECISION_MODEL_CHOICE", "override/choice-model")
    monkeypatch.setenv("DECISION_MODEL_NOUL", "override/noul-model")
    monkeypatch.setenv("DECISION_MODEL_SCORE", "override/score-model")
    monkeypatch.setenv("DECISION_MODEL", "stub/plain-model")  # ignored per-type

    _, snapshots = _run_sse(monkeypatch, decision_client=_stub_batch_client_factory())
    decisions = _final_state(snapshots)["decisions"]

    expected = {
        "choice": "override/choice-model",
        "noul": "override/noul-model",
        "score": "override/score-model",
    }
    assert 1 <= len(decisions) <= 7
    assert {d["type"] for d in decisions} == set(expected)
    for decision in decisions:
        assert decision["model"] == expected[decision["type"]], decision


def test_decision_model_env_streams_without_overrides(seeded_db, monkeypatch):
    """Issue #19: with no per-type overrides, every decisions[].model is the
    DECISION_MODEL env value verbatim — the env swap is observable in the
    drawer without any code edit."""
    for var in ("DECISION_MODEL_CHOICE", "DECISION_MODEL_NOUL", "DECISION_MODEL_SCORE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DECISION_MODEL", "stub/sentinel-model")

    _, snapshots = _run_sse(monkeypatch, decision_client=_stub_batch_client_factory())
    decisions = _final_state(snapshots)["decisions"]

    assert decisions
    assert all(d["model"] == "stub/sentinel-model" for d in decisions), decisions


def test_heuristic_fallback_attributes_heuristic_despite_overrides(
    seeded_db, monkeypatch
):
    """Issue #19 (attribution stays truthful): a transport failure falls back
    to the heuristic, whose declared "heuristic" label wins over any set
    override — decisions never store a guessed model string as fact."""
    monkeypatch.setenv("DECISION_MODEL_CHOICE", "override/choice-model")
    monkeypatch.setenv("DECISION_MODEL_NOUL", "override/noul-model")
    monkeypatch.setenv("DECISION_MODEL_SCORE", "override/score-model")

    def broken_factory():
        class RaisingBatchTransport:
            async def ask_batch(self, questions):
                raise RuntimeError("transport down")

        return DecisionClient(RaisingBatchTransport())

    _, snapshots = _run_sse(monkeypatch, decision_client=broken_factory)
    decisions = _final_state(snapshots)["decisions"]

    assert 1 <= len(decisions) <= 7
    assert all(d["model"] == "heuristic" for d in decisions), decisions


def test_threshold_env_routes_the_layout_cut(seeded_db, monkeypatch):
    """Issue #19 acceptance: DECISION_THRESHOLD is honored end-to-end from
    .env alone. At 0.95 the heuristic's sales route (kpi_row 0.9,
    line_chart 0.85) falls below the line, so the streamed final a2ui is
    exactly the single-insight_text fallback; at 0.6 kpi_row + line_chart
    survive. No code edit between the two runs."""
    monkeypatch.setenv("DECISION_THRESHOLD", "0.95")
    _, high_snapshots = _run_sse(monkeypatch)
    high_items = _final_state(high_snapshots)["a2ui"]["items"]
    assert [item["type"] for item in high_items] == ["insight_text"], high_items

    monkeypatch.setenv("DECISION_THRESHOLD", "0.6")
    _, normal_snapshots = _run_sse(monkeypatch)
    normal_items = _final_state(normal_snapshots)["a2ui"]["items"]
    assert [item["type"] for item in normal_items] == ["kpi_row", "line_chart"]

    # The high-threshold run flagged every answer below the line, the
    # low-threshold run flagged the two endorsed picks above it.
    high_flags = _final_state(high_snapshots)["decisions"]
    assert high_flags and all(not d["above_threshold"] for d in high_flags)

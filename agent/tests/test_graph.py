"""Black-box acceptance tests for the five-node agent graph (issue #10).

Drives the same code path the browser hits — RunAgentInput →
LangGraphAGUIAgent → ag-ui EventEncoder SSE — over the real graph with the
heuristics standing in for both models (no OpenRouter key needed), and
parses every STATE_SNAPSHOT off the wire. Each test names the acceptance
clause it proves; the seeded DB comes from the generator's real entrypoint
per the session-fixture idiom of test_queries.py.
"""

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from ag_ui.core.types import RunAgentInput, UserMessage
from ag_ui.encoder import EventEncoder
from copilotkit import LangGraphAGUIAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import catalog
import db
import graph as agent_graph
from decision.client import DecisionClient, MockTransport

AGENT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = AGENT_DIR / "scripts" / "generate_data.py"
RUN_TIMEOUT_SECONDS = 600

Q3_RANGE = "2026-07-01..2026-09-30"


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    """One seeded temp DB for the session; while it is in place the graph's
    query layer (db.DEFAULT_DB_PATH) reads the seeded data (restored after).
    Same idiom as test_queries.py / test_quality.py."""
    db_path = tmp_path_factory.mktemp("graph") / "seeded.db"
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--db-path", str(db_path)],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    original = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = db_path
    try:
        yield db_path
    finally:
        db.DEFAULT_DB_PATH = original


def _heuristic_client():
    """The deterministic decider the graph falls back to — pinned here so
    the tests need no OpenRouter key and no live endpoint."""
    return DecisionClient(agent_graph.HeuristicTransport())


def _run_sse(monkeypatch, message="Show me sales in Q3", chat_model=None,
             decision_client=None):
    """Run the full graph once through the real AG-UI adapter and return
    (raw SSE text, snapshots). Every snapshot's full state dict is parsed
    from the wire — nothing is poked behind the adapter.

    decision_client: decider factory to monkeypatch in; None (the default)
    pins the deterministic heuristic so no key or live endpoint is needed.
    Tests that need a specific decider (the all-sub-threshold mock) pass
    their own — passed factories are never overwritten by the heuristic.
    """
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
        thread_id=f"test-{uuid.uuid4()}",
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
    fallback = catalog._fallback_layout().model_dump()
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

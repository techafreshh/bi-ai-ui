"""Golden-path A acceptance tests (issue #15): "Show me sales in Q3".

Each of the issue's four acceptance clauses is driven exactly the way the
browser drives it — RunAgentInput → LangGraphAGUIAgent → ag-ui
EventEncoder SSE — against the real seeded DB (the generator's real
entrypoint), with every STATE_SNAPSHOT parsed off the wire:

  1. the completed a2ui holds a kpi_row whose kpis include a label
     containing "ARR" and a line_chart whose range is exactly
     2026-07-01..2026-09-30;
  2. slotCopy holds non-empty strings at "kpis.title" and
     "line_chart.title";
  3. the completed decisions stream holds the catalog choice decision,
     endorsing kpi_row and line_chart above 0.6 each;
  4. three independent runs over the same seeded DB all end on that exact
     Q3 range.

Model-independence: the window never sees a model — questions.resolve_window
parses it from the message with strict regex templates (the _RANGE_RE date
range and the quarter templates) anchored on the seeded data's own latest
event day (2026-09-30), never invented by a model. The heuristics stand in
for both models here anyway (the deterministic decision transport plus a
faked fill model), so the suite needs no OPENROUTER_API_KEY; a live model
could only change the narrative copy, never the window.
"""

import asyncio
import json
import uuid

import pytest
from ag_ui.core.types import RunAgentInput, UserMessage
from ag_ui.encoder import EventEncoder
from copilotkit import LangGraphAGUIAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import db
import graph as agent_graph
from decision.client import DecisionClient

Q3_RANGE = "2026-07-01..2026-09-30"

# The fill model's canned copy brief — the issue's "Q3 revenue took off"
# narrative, routed to every slot key fill now maps (line_chart.title
# included, clause 2's second assertion).
FILL_REPLY = json.dumps(
    {
        "kpis.title": "Q3 revenue took off",
        "line_chart.title": "ARR climbs through Q3",
        "insight_text.body": "Net-new ARR accelerated after the August campaign.",
        "summary": "Q3 revenue took off.",
    }
)


@pytest.fixture(autouse=True)
def _seeded_db(request, seeded_db_path):
    """Point db.DEFAULT_DB_PATH at the session seed (tests/conftest.py, one
    generator subprocess per pytest session) for one test, then restore —
    the graph's query layer reads the path at call time, and the per-test
    restore keeps the swap from leaking into later modules (golden_q3.py
    sorts before every test_*.py; test_generate_data's contract test asserts
    the pristine default)."""
    del request  # autouse-only: every test in this module needs the seed
    original = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = seeded_db_path
    try:
        yield seeded_db_path
    finally:
        db.DEFAULT_DB_PATH = original


def _run_sse(monkeypatch, message="Show me sales in Q3"):
    """Run the full graph once through the real AG-UI adapter and return
    (raw SSE text, STATE_SNAPSHOT payloads). The decision client is pinned
    to the deterministic heuristic transport and the one-shot fill model
    to the canned JSON brief above, so the run needs no key and no live
    endpoint; every snapshot's state dict comes straight off the wire."""
    monkeypatch.setattr(
        agent_graph,
        "get_decision_client",
        lambda: DecisionClient(agent_graph.HeuristicTransport()),
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        agent_graph,
        "build_chat_model",
        lambda: FakeListChatModel(responses=[FILL_REPLY]),
    )

    agent = LangGraphAGUIAgent(
        name="bi_agent", description="golden path A test", graph=agent_graph.graph
    )
    run_input = RunAgentInput(
        thread_id=f"golden-{uuid.uuid4()}",
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


# --- Clause 1: completed a2ui routes to an ARR kpi_row + exact-Q3 line_chart --


def test_golden_q3_streams_arr_kpis_and_exact_q3_chart(monkeypatch):
    """The completed a2ui holds a kpi_row whose kpis include a label
    containing "ARR" and a line_chart with range exactly
    2026-07-01..2026-09-30; the run finishes cleanly."""
    raw, snapshots = _run_sse(monkeypatch)

    assert "RUN_FINISHED" in raw
    items = _final_state(snapshots)["a2ui"]["items"]

    kpi_rows = [item for item in items if item["type"] == "kpi_row"]
    assert len(kpi_rows) >= 1, items
    assert any(
        "ARR" in tile["label"] for tile in kpi_rows[0]["props"]["kpis"]
    ), kpi_rows[0]["props"]["kpis"]

    charts = [item for item in items if item["type"] == "line_chart"]
    assert len(charts) >= 1, items
    assert charts[0]["props"]["range"] == Q3_RANGE


# --- Clause 2: fill writes non-empty copy into both titled slots --------------


def test_golden_q3_slot_copy_titles_are_non_empty(monkeypatch):
    """The completed a2ui's slotCopy carries non-empty strings at
    "kpis.title" and "line_chart.title" — fill's narrative lands in both."""
    _, snapshots = _run_sse(monkeypatch)
    slot_copy = _final_state(snapshots)["a2ui"].get("slotCopy", {})
    for key in ("kpis.title", "line_chart.title"):
        value = slot_copy.get(key)
        assert isinstance(value, str) and value.strip(), {key: value}


# --- Clause 3: the choice decision endorses the routed pair above 0.6 ---------


def test_golden_q3_choice_decision_endorses_kpi_row_and_line_chart(monkeypatch):
    """The completed decisions stream holds a choice decision for the
    catalog question whose probabilities for kpi_row and line_chart each
    exceed 0.6 (0.9 / 0.85 under the heuristic)."""
    _, snapshots = _run_sse(monkeypatch)
    choice = next(
        entry
        for entry in _final_state(snapshots)["decisions"]
        if entry["id"] == agent_graph.questions.CATALOG_QUESTION_ID
    )
    assert choice["type"] == "choice", choice
    assert choice["probabilities"]["kpi_row"] > 0.6, choice
    assert choice["probabilities"]["line_chart"] > 0.6, choice


# --- Clause 4: the Q3 window is deterministic across runs ----------------------


def test_golden_q3_window_is_deterministic_across_three_runs(monkeypatch):
    """Three independent runs over the same seeded DB all yield the
    line_chart with the same exact Q3 range — the window is regex-parsed
    from the message, never model-invented."""
    for _ in range(3):
        _, snapshots = _run_sse(monkeypatch)
        items = _final_state(snapshots)["a2ui"]["items"]
        ranges = [
            item["props"]["range"]
            for item in items
            if item["type"] == "line_chart"
        ]
        assert ranges == [Q3_RANGE]

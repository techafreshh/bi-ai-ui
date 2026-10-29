"""Sample-sandbox golden paths (issue #86).

The three golden questions must reproduce on demand from the reviewer-scale
sample sandbox (`samples/*.csv`, `data_end` 2026-09-14) — not just on the
seeded demo DB. Each clause is driven exactly the way the browser drives it
(RunAgentInput → LangGraphAGUIAgent → ag-ui EventEncoder SSE) with the
heuristic decider and a faked fill model, so the suite needs no
OPENROUTER_API_KEY and no network:

  1. "Show me sales in Q3" streams a final a2ui holding both a kpi_row and
     a line_chart, with slotCopy naming the Q3 window (2026-07-01);
  2. "why did churn spike?" streams a variance_callout (repricing cause)
     and a data_table with at most 50 rows;
  3. "Is the data clean?" streams a data_quality_card reading
     duplicates:fail and nulls:fail, scoped to the Sept 8-12 Startup dirt
     with a Sep-naming detail line.

The fixture builds ONE sandbox from the repo-root samples/*.csv through
sandbox.load_tables (the same parse path POST /data/sample uses) and
activates it in the registry for the module, restoring the registry after —
mirroring tests/test_sample.py's isolation and golden_q3.py's SSE harness.
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

import datasets
import graph as agent_graph
from decision.client import DecisionClient
from sandbox import ALLOWED_TABLES, _parse_text, load_tables

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_SAMPLES = REPO_ROOT / "samples"

# The sample union-MAX event date — the data_end every window below anchors
# on (POST /data/sample reports the same value from datasets.profile_db).
SAMPLE_DATA_END = "2026-09-14"

# The fill model's canned copy brief — every slot key fill maps, so the
# run's copy assertions read deterministic text, never model prose. The
# "data_quality_card.summary" entry is poison on purpose: fill must drop it
# (the DQ summary is deterministic copy, golden_dq.py's contract).
FILL_REPLY = json.dumps(
    {
        "kpis.title": "Sample Q3 revenue",
        "line_chart.title": "ARR through the sample window",
        "insight_text.body": "Sample numbers moved with the August campaign.",
        "summary": "Sample summary.",
        "data_quality_card.summary": "MODEL OVERRIDE MUST BE DROPPED",
    }
)


@pytest.fixture(scope="module")
def _active_sample(tmp_path_factory):
    """Build one sandbox from the repo-root samples/*.csv and activate it
    for the module (registry save/restore keeps the id out of later
    modules). The sandbox dir is module-scoped tmp so no repo-root data/
    file is touched."""
    sandbox_dir = tmp_path_factory.mktemp("sample-golden")
    rows_by_table = {}
    for table in ALLOWED_TABLES:
        csv_path = REPO_SAMPLES / f"{table}.csv"
        rows, _ = _parse_text(table, csv_path.read_text(encoding="utf-8"), str(csv_path))
        rows_by_table[table] = rows
    dataset_id = "sample-golden"
    dest = load_tables(rows_by_table, dataset_id, sandbox_dir=sandbox_dir)
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets.register(dataset_id, dest, "sample")
    assert datasets.set_active(dataset_id)
    try:
        yield dest
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active


@pytest.fixture(autouse=True)
def _needs_sample(request, _active_sample):
    """Every test in this module runs against the active sample sandbox."""
    del request  # autouse-only: the sample activation is the fixture's job


def _run_sse(monkeypatch, message, fill_reply=FILL_REPLY):
    """Run the full graph once through the real AG-UI adapter and return
    (raw SSE text, STATE_SNAPSHOT payloads) — golden_q3.py's harness with
    the message parameterized per golden question. fill_reply None leaves
    the text model unpatched so the no-key path writes the deterministic
    copy (the window-anchored titles the Q3 clause asserts)."""
    monkeypatch.setattr(
        agent_graph,
        "get_decision_client",
        lambda: DecisionClient(agent_graph.HeuristicTransport()),
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    if fill_reply is not None:
        monkeypatch.setattr(
            agent_graph,
            "build_chat_model",
            lambda: FakeListChatModel(responses=[fill_reply]),
        )

    agent = LangGraphAGUIAgent(
        name="bi_agent", description="sample golden test", graph=agent_graph.graph
    )
    run_input = RunAgentInput(
        thread_id=f"sample-golden-{uuid.uuid4()}",
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


# --- Clause 1: Q3 sales on the sample sandbox --------------------------------


def test_sample_q3_streams_kpi_row_and_line_chart(monkeypatch):
    """'Show me sales in Q3' on the sample sandbox (data_end 2026-09-14)
    streams a final a2ui whose item types include both kpi_row and
    line_chart, and whose slotCopy names the Q3 window (2026-07-01)."""
    raw, snapshots = _run_sse(monkeypatch, "Show me sales in Q3", fill_reply=None)

    assert "RUN_FINISHED" in raw
    final = _final_state(snapshots)
    assert final["snapshot"]["data_end"] == SAMPLE_DATA_END
    types = {item["type"] for item in final["a2ui"]["items"]}
    assert "kpi_row" in types, types
    assert "line_chart" in types, types
    assert "2026-07-01" in json.dumps(final["a2ui"].get("slotCopy", {}))
    charts = [
        item for item in final["a2ui"]["items"] if item["type"] == "line_chart"
    ]
    assert charts[0]["props"]["range"] == "2026-07-01..2026-09-30"


# --- Clause 2: period-less churn on the sample sandbox ------------------------


def test_sample_churn_spike_streams_callout_and_bounded_cohort(monkeypatch):
    """'why did churn spike?' (no explicit period) on the sample sandbox
    streams a variance_callout whose cause names the repricing and a
    data_table with at most 50 rows — the whole August spike cohort."""
    raw, snapshots = _run_sse(monkeypatch, "why did churn spike?")

    assert "RUN_FINISHED" in raw
    items = _final_state(snapshots)["a2ui"]["items"]
    types = {item["type"] for item in items}
    assert "variance_callout" in types, types
    assert "data_table" in types, types
    callouts = [item for item in items if item["type"] == "variance_callout"]
    assert "pricing" in (callouts[0]["props"].get("cause") or "").lower()
    tables = [item for item in items if item["type"] == "data_table"]
    assert 1 <= len(tables[0]["props"]["rows"]) <= 50


# --- Clause 3: data-cleanliness on the sample sandbox -------------------------


def test_sample_data_clean_reports_failures_in_dirty_window(monkeypatch):
    """'Is the data clean?' on the sample sandbox streams a
    data_quality_card whose checks read duplicates:fail and nulls:fail,
    scoped to the detected Sept 8-12 Startup dirt with a Sep-naming detail
    line — and the deterministic summary (not the model's poison) on the
    wire."""
    raw, snapshots = _run_sse(monkeypatch, "Is the data clean?")

    assert "RUN_FINISHED" in raw
    final = _final_state(snapshots)
    cards = [
        item for item in final["a2ui"]["items"] if item["type"] == "data_quality_card"
    ]
    assert len(cards) >= 1, final["a2ui"]["items"]
    pairs = {
        f"{row['name']}:{row['status']}" for row in cards[0]["props"]["checks"]
    }
    assert "duplicates:fail" in pairs, pairs
    assert "nulls:fail" in pairs, pairs
    assert final["snapshot"]["dq_scope"] == {
        "start": "2026-09-08",
        "end": "2026-09-12",
        "segment": "Startup",
    }
    detail = cards[0]["props"].get("data_problem_detail", "")
    assert "Sep" in detail and "Startup" in detail, detail
    summary = final["a2ui"].get("slotCopy", {})[agent_graph.DQ_SLOT_KEY].lower()
    for needle in ("duplicate", "null", "startup", "sep"):
        assert needle in summary, summary
    assert "MODEL OVERRIDE" not in summary

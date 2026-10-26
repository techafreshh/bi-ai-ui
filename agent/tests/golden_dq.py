"""Golden-path C acceptance tests (issue #17): "Is the data clean?".

Each of the issue's four acceptance clauses is driven exactly the way the
browser drives it — RunAgentInput → LangGraphAGUIAgent → ag-ui EventEncoder
SSE — against the real seeded DB (the generator's real entrypoint, which
plants the Sept 8–21, 2026 Startup-segment ingest bug), with every
STATE_SNAPSHOT parsed off the wire:

  1. the completed a2ui holds a data_quality_card whose checks read
     duplicates=fail, nulls=fail, freshness=ok — the card is scoped to the
     detected anomaly window (snapshot["dq_scope"]) instead of a diluting
     trailing-30 display window;
  2. the completed decisions stream holds the noul "data_problem" decision
     whose data_problem-ward probability exceeds 0.7;
  3. the card's wire props carry the banner gate inputs: data_problem >
     0.6 and a narrated detail containing "Sep" and "Startup" (the browser
     half of the clause — the dq-warning DOM banner — is covered by the
     component and canvas suites in web/src);
  4. slotCopy["data_quality_card.summary"] mentions "duplicate", "null",
     "startup" and "sep" (case-insensitive).

Model-independence: the heuristics stand in for both models (the
deterministic decision transport scores the quality route at 0.94 on the
flipped data_problem noul; the faked fill model's JSON brief deliberately
carries a poison "data_quality_card.summary" key whose override must be
dropped — the summary is deterministic copy, never model prose), so the
suite needs no OPENROUTER_API_KEY.
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

import db
import graph as agent_graph
from decision.client import DecisionClient

AGENT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = AGENT_DIR / "scripts" / "generate_data.py"
RUN_TIMEOUT_SECONDS = 600

MESSAGE = "Is the data clean?"

# The planted ingest-bug scope the detection must reproduce (issue #4's
# plant, re-derived from the data by quality.recent_problem_scope).
BUG_SCOPE = {"start": "2026-09-08", "end": "2026-09-21", "segment": "Startup"}

# The fill model's canned copy brief. The "data_quality_card.summary" entry
# is poison on purpose: the model is never asked for that key and its value
# must be dropped even when present — the DQ summary is deterministic copy.
FILL_REPLY = json.dumps(
    {
        "kpis.title": "Data quality check",
        "line_chart.title": "not requested",
        "insight_text.body": "Checks ran over the detected problem window.",
        "summary": "The data is not clean — duplicates and nulls are failing.",
        "data_quality_card.summary": "MODEL OVERRIDE MUST BE DROPPED",
    }
)


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory):
    """Seed ONE temp DB for the session through the generator's real
    entrypoint and return its path. Same idiom as golden_q3.py — the
    db.DEFAULT_DB_PATH swap is kept out of this fixture (golden_dq.py sorts
    before every test_*.py, so a session-long swap would leak into later
    modules); the _seeded_db fixture below swaps per test and restores."""
    db_path = tmp_path_factory.mktemp("golden_dq") / "seeded.db"
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--db-path", str(db_path)],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return db_path


@pytest.fixture(autouse=True)
def _seeded_db(request, seeded_db_path):
    """Point db.DEFAULT_DB_PATH at the session seed for one test, then
    restore — the graph's query layer reads the path at call time."""
    del request  # autouse-only: every test in this module needs the seed
    original = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = seeded_db_path
    try:
        yield seeded_db_path
    finally:
        db.DEFAULT_DB_PATH = original


def _run_sse(monkeypatch, message=MESSAGE):
    """Run the full graph once through the real AG-UI adapter and return
    (raw SSE text, STATE_SNAPSHOT payloads). The decision client is pinned
    to the deterministic heuristic transport and the one-shot fill model to
    the canned JSON brief above, so the run needs no key and no live
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
        name="bi_agent", description="golden path C test", graph=agent_graph.graph
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


def _dq_card_items(state):
    """The run's surviving data_quality_card items."""
    return [
        item
        for item in state["a2ui"]["items"]
        if item["type"] == "data_quality_card"
    ]


# --- Clause 1: the card's checks read duplicates fail / nulls fail /
# --- freshness ok, scoped to the detected Sept 8-21 anomaly window ---------


def test_golden_dq_card_reveals_the_planted_bug(monkeypatch):
    """The completed a2ui holds a data_quality_card whose checks — by name
    — read duplicates fail, nulls fail and freshness ok, and the run's
    snapshot carries the detected dq_scope exactly on the planted window."""
    raw, snapshots = _run_sse(monkeypatch)

    assert "RUN_FINISHED" in raw
    final = _final_state(snapshots)
    cards = _dq_card_items(final)
    assert len(cards) >= 1, final["a2ui"]["items"]
    checks = {row["name"]: row["status"] for row in cards[0]["props"]["checks"]}
    assert checks["duplicates"] == "fail", checks
    assert checks["nulls"] == "fail", checks
    assert checks["freshness"] == "ok", checks

    # The scope is the data-detected Sept 8-21 Startup bug — not the Q3
    # display window, not a hardcoded string.
    assert final["snapshot"]["dq_scope"] == BUG_SCOPE


# --- Clause 2: the data_problem noul decision scores high -------------------


def test_golden_dq_noul_decision_scores_data_problem_high(monkeypatch):
    """The completed decisions stream holds a noul decision with id
    "data_problem" whose data_problem-ward probability exceeds 0.7 — the
    heuristic's quality route reads 0.94 directly (no 1 - p inversion)."""
    _, snapshots = _run_sse(monkeypatch)
    decisions = _final_state(snapshots)["decisions"]
    matches = [
        entry
        for entry in decisions
        if entry["id"] == agent_graph.questions.DATA_PROBLEM_QUESTION_ID
    ]
    assert len(matches) == 1, decisions
    decision = matches[0]
    assert decision["type"] == "noul", decisions
    probability = decision["probabilities"]
    assert isinstance(probability, float) and not isinstance(probability, bool)
    assert probability > 0.7, decision


# --- Clause 3 (server half): the card props carry the banner gate ----------


def test_golden_dq_card_props_carry_banner_gate_and_window_detail(monkeypatch):
    """The rendered card's wire props carry data_problem > 0.6 (the banner
    gate the frontend reads) and a narrated detail containing "Sep" and
    "Startup" — the evidence the warning banner shows."""
    _, snapshots = _run_sse(monkeypatch)
    props = _dq_card_items(_final_state(snapshots))[0]["props"]
    assert props["data_problem"] > 0.6, props
    detail = props["data_problem_detail"]
    assert isinstance(detail, str) and "Sep" in detail, props
    assert "Startup" in detail, props


# --- Clause 4: the deterministic slot copy names duplicates and nulls ------


def test_golden_dq_slot_copy_names_duplicates_nulls_and_window(monkeypatch):
    """slotCopy on the data_quality_card mentions both "duplicate" and
    "null" (case-insensitive) plus the narrated window/segment words, and
    the fake model's poison summary key never overrides it."""
    _, snapshots = _run_sse(monkeypatch)
    slot_copy = _final_state(snapshots)["a2ui"].get("slotCopy", {})
    summary = slot_copy[agent_graph.DQ_SLOT_KEY].lower()
    for needle in ("duplicate", "null", "startup", "sep"):
        assert needle in summary, {agent_graph.DQ_SLOT_KEY: summary}
    assert "MODEL OVERRIDE" not in summary

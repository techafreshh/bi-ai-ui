"""Acceptance tests for threshold fallback and off-catalog safety (issue #18).

"Draw me a dragon" (anything heuristic.classify routes to off_catalog) must
never render a chart/table/callout/kpi: the decide node's guard transport
answers it deterministically, the threshold cut empties, and every emitted
layout stays inside the A2UI catalog. Decision/transport failures degrade to
the insight_text fallback instead of crashing a request. Every test below
drives the real AG-UI SSE path (RunAgentInput -> LangGraphAGUIAgent ->
EventEncoder) over the seeded DB, reusing test_graph's harness; the hammer
test posts to the real `/copilotkit` app (server.py) 100 times.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

import catalog
import datasets
import graph as agent_graph
import questions as questions_module
from decision.client import DecisionClient, MockTransport
from server import app
from tests.test_graph import (
    _emitted_a2ui,
    _final_state,
    _heuristic_client,
    _run_sse,
)

# The seeded DB itself is the suite-wide shared seed: tests/conftest.py
# provides the `seeded_db` fixture these tests consume by name.


@pytest.fixture(scope="module", autouse=True)
def _active_seed(seeded_db):
    """Issue #85 rescoped the graph to the single-active dataset: activate
    the module seed so SSE runs (including the hammer's /copilotkit posts)
    read it; save/restore keeps the id out of later modules."""
    saved_by_id = dict(datasets._BY_ID)
    saved_active = datasets._ACTIVE_ID
    datasets.register("seeded", seeded_db, "sample")
    assert datasets.set_active("seeded")
    try:
        yield
    finally:
        datasets._BY_ID.clear()
        datasets._BY_ID.update(saved_by_id)
        datasets._ACTIVE_ID = saved_active


# --- Acceptance clause 1 (+3): the dragon renders only the insight fallback ---


def test_draw_me_a_dragon_renders_only_insight_text(seeded_db, monkeypatch):
    """'draw me a dragon' under the default threshold(0.6) wiring: the
    completed a2ui has v=1, layout 'grid' and ONLY insight_text items (zero
    chart/table/callout/kpi), shaped exactly like the catalog fallback with
    a non-empty body, and fill still closes the chat with a summary."""
    raw, snapshots = _run_sse(monkeypatch, message="draw me a dragon")

    assert "RUN_FINISHED" in raw
    assert "RUN_ERROR" not in raw
    final_a2ui = _final_state(snapshots)["a2ui"]
    assert final_a2ui["v"] == 1
    assert final_a2ui["layout"] == "grid"
    assert [item["type"] for item in final_a2ui["items"]] == ["insight_text"]
    assert final_a2ui["items"] == catalog.fallback_layout().model_dump()["items"]
    props = final_a2ui["items"][0]["props"]
    assert props["title"] == catalog.FALLBACK_TITLE
    assert props["body"]  # the fallback speaks: non-empty narrative
    assert final_a2ui["slotCopy"]["summary"]


# --- Clause 1, determinism: a live decider's misjudgment cannot leak ---------


class _CountingMockTransport(MockTransport):
    """MockTransport that counts every ask — the probe proving the gate
    answered the query before ANY wired-transport I/O happened."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = 0

    async def ask(self, question):
        self.calls += 1
        return await super().ask(question)


def test_off_catalog_query_never_consults_the_wired_decider(seeded_db, monkeypatch):
    """A wired decider that would endorse kpi_row: 0.9 / line_chart: 0.85
    for 'draw me a dragon' must never be consulted: transport.calls == 0,
    every streamed decision is attributed to 'heuristic' with
    above_threshold False, and the canvas still holds only the insight
    fallback (without the gate, the wired mock's calls >= 1 and kpi_row
    would render — this test discriminates)."""
    question_set = agent_graph.questions.build_question_set("draw me a dragon", {})
    answers = {}
    for index, question in enumerate(question_set):
        # A decider that misjudges the dragon: two components above the 0.6
        # threshold, everything else zero, scalars inside [0.5, 0.9].
        probabilities = (
            {
                option: (
                    0.9
                    if option == "kpi_row"
                    else 0.85 if option == "line_chart" else 0.0
                )
                for option in question.options
            }
            if question.type == "choice"
            else 0.5 + 0.1 * index
        )
        answers[question.id] = {
            "id": question.id,
            "type": question.type,
            "probabilities": probabilities,
        }

    transport = _CountingMockTransport(answers=answers)

    def wired_client():
        return DecisionClient(transport)

    _, snapshots = _run_sse(
        monkeypatch, message="draw me a dragon", decision_client=wired_client
    )

    assert transport.calls == 0  # the gate fired before any transport I/O
    decisions = _final_state(snapshots)["decisions"]
    assert decisions, "the guard still stamps decisions"
    for decision in decisions:
        assert decision["model"] == "heuristic", decision
        assert decision["above_threshold"] is False, decision
    assert [item["type"] for item in _final_state(snapshots)["a2ui"]["items"]] == [
        "insight_text"
    ]


# --- Acceptance clause 2: every streamed layout stays inside the 6-catalog ---


def test_off_catalog_stream_stays_inside_the_six_type_catalog(
    seeded_db, monkeypatch
):
    """Every a2ui the dragon run ever streams parses through A2UILayout (the
    node-side assertion's black-box shadow: no ValidationError reaches the
    client) and every item type is one of the fixed 6-catalog set."""
    _, snapshots = _run_sse(monkeypatch, message="draw me a dragon")
    emitted = _emitted_a2ui(snapshots)
    assert emitted, "expected at least one a2ui on the wire"
    for a2ui in emitted:
        catalog.A2UILayout.model_validate(a2ui)  # raises on any violation
        for item in a2ui["items"]:
            assert item["type"] in set(questions_module.CATALOG_TYPES), item


# --- Acceptance clause 3 (pure form): an absent catalog answer drops fail-safe


class _MissingAnswerTransport(MockTransport):
    """MockTransport answered through the batch seam: question ids absent
    from `answers` are simply absent from the mapping, so DecisionClient's
    missing-answer rule (one WARNING per dropped question) applies instead
    of per-question MockTransport's loud KeyError — the wire shape of a
    decider that never scored one question."""

    async def ask_batch(self, questions):
        return {q.id: self.answers[q.id] for q in questions if q.id in self.answers}


def test_missing_catalog_answer_falls_back_to_grid_single_insight(
    seeded_db, monkeypatch
):
    """An empty above-threshold SET — here because the decider answered only
    the three scalar questions and never the catalog one: `decisions`
    streams exactly 3 entries (within 1..7), the layout cut reads no
    endorsement at all and the final a2ui is the grid with exactly one
    non-empty-body insight_text item."""
    answers = {
        questions_module.DATA_PROBLEM_QUESTION_ID: {
            "id": questions_module.DATA_PROBLEM_QUESTION_ID,
            "type": "noul",
            "probabilities": 0.7,
        },
        questions_module.ANOMALY_QUESTION_ID: {
            "id": questions_module.ANOMALY_QUESTION_ID,
            "type": "noul",
            "probabilities": 0.6,
        },
        questions_module.DQ_CONFIDENCE_QUESTION_ID: {
            "id": questions_module.DQ_CONFIDENCE_QUESTION_ID,
            "type": "score",
            "probabilities": 0.8,
        },
    }

    def partial_client():
        return DecisionClient(_MissingAnswerTransport(answers=answers))

    _, snapshots = _run_sse(monkeypatch, message="Show me sales in Q3",
                            decision_client=partial_client)

    decisions = _final_state(snapshots)["decisions"]
    assert 1 <= len(decisions) <= 7
    assert len(decisions) == 3, decisions
    final_a2ui = _final_state(snapshots)["a2ui"]
    catalog.A2UILayout.model_validate(final_a2ui)
    assert final_a2ui["layout"] == "grid"
    items = final_a2ui["items"]
    assert [item["type"] for item in items] == ["insight_text"]
    assert len(items) == 1
    assert items[0]["props"]["body"]


# --- Acceptance clause 4, resilience branch: transport outage degrades -------


class _OutageTransport:
    """A decision transport under a simulated outage: every ask raises."""

    model = "outage"

    async def ask(self, question):
        raise RuntimeError("simulated transport outage")


def test_transport_error_degrades_to_heuristic_fallback(seeded_db, monkeypatch):
    """A raising transport is the decide node's pre-existing fallback path
    (issue #10), here pinned at the wire: the run finishes (no RUN_ERROR),
    decisions still stream 1..7 entries from the heuristic, the final a2ui
    validates and carries the heuristic's sales route (kpi_row +
    line_chart)."""
    def outage_client():
        return DecisionClient(_OutageTransport())

    raw, snapshots = _run_sse(
        monkeypatch, message="Show me sales in Q3", decision_client=outage_client
    )

    assert "RUN_FINISHED" in raw
    assert "RUN_ERROR" not in raw
    decisions = _final_state(snapshots)["decisions"]
    assert 1 <= len(decisions) <= 7
    for decision in decisions:
        assert decision["model"] == "heuristic", decision
    final_a2ui = _final_state(snapshots)["a2ui"]
    catalog.A2UILayout.model_validate(final_a2ui)
    types = [item["type"] for item in final_a2ui["items"]]
    assert "kpi_row" in types and "line_chart" in types


# --- Acceptance clause 4: 100 sequential POSTs to /copilotkit never fail -----


# The four in-repo probe messages: three golden in-domain queries (each
# heuristic route exercised) cycling with the pinned off-catalog ask.
HAMMER_MESSAGES = [
    "Show me sales in Q3",
    "Why did churn spike after the pricing change?",
    "Is the data clean? Check for duplicates and nulls",
    "draw me a dragon",
]


def test_hundred_sequential_copilotkit_requests_never_fail(seeded_db, monkeypatch):
    """100 sequential valid POSTs to the real /copilotkit app: every request
    answers HTTP 200 (TestClient's raise_server_exceptions default turns any
    handler crash into this test failing outright — stronger than a 500
    count), each stream ends in RUN_FINISHED with no RUN_ERROR, the last
    STATE_SNAPSHOT's a2ui validates through A2UILayout, and dragon runs
    carry only the insight fallback."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(agent_graph, "get_decision_client", _heuristic_client)
    client = TestClient(app)

    dragon_runs = 0
    for i in range(100):
        message = HAMMER_MESSAGES[i % len(HAMMER_MESSAGES)]
        body = {
            "threadId": f"hammer-{i}",
            "runId": f"hammer-run-{i}-{uuid.uuid4()}",
            "state": {},
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "messages": [
                {"id": f"hammer-m-{i}", "role": "user", "content": message}
            ],
        }
        with client.stream(
            "POST",
            "/copilotkit",
            json=body,
            headers={
                "Accept": "text/event-stream",
                "Origin": "http://localhost:5173",
            },
        ) as response:
            assert response.status_code == 200, (i, message, response.status_code)
            text = "".join(response.iter_text())
        payloads = [
            json.loads(line.removeprefix("data: "))
            for line in text.splitlines()
            if line.startswith("data: ")
        ]
        types = [payload["type"] for payload in payloads]
        assert "RUN_FINISHED" in types, (i, message, types)
        assert "RUN_ERROR" not in types, (i, message, types)
        snapshots = [
            payload["snapshot"]
            for payload in payloads
            if payload["type"] == "STATE_SNAPSHOT"
        ]
        a2ui = snapshots[-1]["a2ui"]
        catalog.A2UILayout.model_validate(a2ui)
        if message == "draw me a dragon":
            dragon_runs += 1
            assert [item["type"] for item in a2ui["items"]] == ["insight_text"], i
    assert dragon_runs == 25  # every fourth probe is the dragon

"""Acceptance-contract tests for the decision layer (issue #8).

One test per acceptance clause of issue #8 (plus the parsing rule table,
threshold policy, schema validation and import-behavior pins the plan
commits to), each with a docstring naming the clause it proves. Pure
in-process tests: the decision package imports nothing from this repo but
stdlib, so nothing here touches the DB, the server or the graph; async
entry is plain asyncio.run (the test_model_contract.py idiom — no
pytest-asyncio).
"""

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

import decision

AGENT_DIR = Path(__file__).resolve().parents[1]
DECISION_LOGGER = "bi-ai-ui.agent.decision"


def _mixed_questions():
    """One question of each wire type — choice (which components), noul
    (yes/no) and score (0-1 rating) — the mixed batch acceptance clause 1
    decides."""
    return [
        decision.ChoiceQuestion(
            id="which-segment",
            prompt="Which segment explains the Q3 ARR ramp?",
            options=["Growth", "Startup", "Enterprise"],
        ),
        decision.NoulQuestion(id="is-data-clean", prompt="Is the data clean?"),
        decision.ScoreQuestion(id="momentum-score", prompt="Score the momentum, 0-1."),
    ]


def test_decide_answers_three_mixed_questions(monkeypatch):
    """Acceptance clause 1: 3 mixed questions against a MockTransport
    configured with the expected payloads return 3 answers whose ids and
    types match the questions one-to-one, with dict probabilities for the
    choice and single floats for noul/score."""
    monkeypatch.delenv("DECISION_THRESHOLD", raising=False)
    questions = _mixed_questions()
    answers_map = {
        "which-segment": {
            "id": "which-segment",
            "type": "choice",
            "probabilities": {"Growth": 0.8, "Startup": 0.1, "Enterprise": 0.1},
        },
        "is-data-clean": {"id": "is-data-clean", "type": "noul", "probabilities": 0.94},
        "momentum-score": {"id": "momentum-score", "type": "score", "probabilities": 0.72},
    }
    client = decision.DecisionClient(decision.MockTransport(answers=answers_map))
    answers = asyncio.run(client.decide(questions))

    assert all(isinstance(answer, decision.Answer) for answer in answers)
    assert [answer.id for answer in answers] == [q.id for q in questions]
    assert [answer.type for answer in answers] == [q.type for q in questions]
    assert answers[0].probabilities == {"Growth": 0.8, "Startup": 0.1, "Enterprise": 0.1}
    assert answers[1].probabilities == 0.94
    assert answers[2].probabilities == 0.72
    # Every configured answer clears the default 0.6 line.
    assert all(answer.above_threshold for answer in answers)


def test_five_slow_answers_return_in_parallel():
    """Acceptance clause 2: 5 questions against a MockTransport with a
    500ms delay each return in < 600ms wall clock — parallel execution
    inside the client (a sequential ask would take 5 x 500ms = 2.5s); the
    0.5s lower bound proves every transport delay actually ran."""
    questions = [
        decision.NoulQuestion(id=f"noul-{i}", prompt=f"Question {i}?") for i in range(5)
    ]
    answers_map = {
        f"noul-{i}": {"id": f"noul-{i}", "type": "noul", "probabilities": 0.9}
        for i in range(5)
    }
    client = decision.DecisionClient(
        decision.MockTransport(timeout_ms=500, answers=answers_map)
    )

    start = time.monotonic()
    answers = asyncio.run(client.decide(questions))
    elapsed = time.monotonic() - start

    assert len(answers) == 5
    assert 0.5 <= elapsed < 0.6, f"elapsed {elapsed:.3f}s (sequential would be ~2.5s)"


def test_unknown_answer_id_dropped_with_warning(caplog):
    """Acceptance clause 3: a transport payload whose id matches no
    question id is dropped with a WARNING naming both ids; the returned
    list length reflects only the valid answers, in question order."""
    questions = [
        decision.NoulQuestion(id="question-1", prompt="Q1?"),
        decision.NoulQuestion(id="question-2", prompt="Q2?"),
        decision.NoulQuestion(id="question-3", prompt="Q3?"),
    ]
    answers_map = {
        # The transport "lies" about question-1's payload id.
        "question-1": {"id": "rogue-answer", "type": "noul", "probabilities": 0.9},
        "question-2": {"id": "question-2", "type": "noul", "probabilities": 0.8},
        "question-3": {"id": "question-3", "type": "noul", "probabilities": 0.7},
    }
    client = decision.DecisionClient(decision.MockTransport(answers=answers_map))
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        answers = asyncio.run(client.decide(questions))

    assert [answer.id for answer in answers] == ["question-2", "question-3"]
    drops = [
        record for record in caplog.records if record.name == DECISION_LOGGER
    ]
    assert len(drops) == 1
    message = drops[0].getMessage()
    assert "rogue-answer" in message and "question-1" in message


def test_below_threshold_noul_flags_false(monkeypatch):
    """Acceptance clause 4: with DECISION_THRESHOLD=0.6, a noul answer at
    p=0.55 reads above_threshold=False while its companion at p=0.65 reads
    True — the flag follows the env-configured line."""
    monkeypatch.setenv("DECISION_THRESHOLD", "0.6")
    questions = [
        decision.NoulQuestion(id="sparse-noul", prompt="Data sparse?"),
        decision.NoulQuestion(id="healthy-noul", prompt="Data healthy?"),
    ]
    answers_map = {
        "sparse-noul": {"id": "sparse-noul", "type": "noul", "probabilities": 0.55},
        "healthy-noul": {"id": "healthy-noul", "type": "noul", "probabilities": 0.65},
    }
    client = decision.DecisionClient(decision.MockTransport(answers=answers_map))
    answers = asyncio.run(client.decide(questions))

    assert [answer.id for answer in answers] == ["sparse-noul", "healthy-noul"]
    assert answers[0].above_threshold is False
    assert answers[1].above_threshold is True


def test_exactly_at_threshold_passes_with_default(monkeypatch):
    """Rule pin: the line is inclusive (>= — PRD "p<0.6 drops a component",
    so exactly 0.6 passes) and DEFAULT_THRESHOLD=0.6 is what runs with no
    explicit threshold and no DECISION_THRESHOLD in the env."""
    monkeypatch.delenv("DECISION_THRESHOLD", raising=False)
    questions = [decision.NoulQuestion(id="at-line", prompt="Clean data?")]
    client = decision.DecisionClient(
        decision.MockTransport(
            answers={"at-line": {"id": "at-line", "type": "noul", "probabilities": 0.6}}
        )
    )
    assert client.threshold == decision.DEFAULT_THRESHOLD == 0.6
    answer = asyncio.run(client.decide(questions))[0]
    assert answer.above_threshold is True


def test_type_mismatch_dropped_with_warning(caplog):
    """Rule pin: a payload whose type disagrees with its question's (a
    choice-shaped answer returned for a noul question) is dropped with a
    WARNING — clause 1 requires matching ids AND types."""
    questions = [decision.NoulQuestion(id="q-noul", prompt="Clean data?")]
    answers_map = {
        "q-noul": {
            "id": "q-noul",
            "type": "choice",
            "probabilities": {"Growth": 0.9, "Startup": 0.1},
        }
    }
    client = decision.DecisionClient(decision.MockTransport(answers=answers_map))
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        answers = asyncio.run(client.decide(questions))

    assert answers == []
    message = caplog.records[0].getMessage()
    assert "q-noul" in message and "choice" in message and "noul" in message


def test_scalar_out_of_range_dropped_never_clamped(caplog):
    """Rule pin: a score of 1.5 is dropped whole, never clamped to 1.0 — a
    clamped value would fake confidence; the drop is warned, not silent."""
    questions = [decision.ScoreQuestion(id="momentum", prompt="Score 0-1.")]
    answers_map = {
        "momentum": {"id": "momentum", "type": "score", "probabilities": 1.5}
    }
    client = decision.DecisionClient(decision.MockTransport(answers=answers_map))
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        answers = asyncio.run(client.decide(questions))

    assert answers == []
    assert "1.5" in caplog.records[0].getMessage() and "momentum" in caplog.records[0].getMessage()


def test_choice_probabilities_normalized(caplog):
    """Rule pin (choice parsing): unknown option keys are dropped with a
    WARNING while the answer survives, missing options are filled 0.0 (a
    total dict for argmax consumers), and a value outside [0, 1] drops the
    whole answer."""
    question = decision.ChoiceQuestion(
        id="which-segment",
        prompt="Which segment explains the ramp?",
        options=["Growth", "Startup", "Enterprise"],
    )
    client = decision.DecisionClient(
        decision.MockTransport(
            answers={
                "which-segment": {
                    "id": "which-segment",
                    "type": "choice",
                    "probabilities": {"Growth": 0.9, "rogue-key": 0.5},
                }
            }
        )
    )
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        (answer,) = asyncio.run(client.decide([question]))
    assert answer.probabilities == {
        "Growth": 0.9,
        "Startup": 0.0,
        "Enterprise": 0.0,
    }
    assert "rogue-key" in caplog.records[0].getMessage()

    caplog.clear()
    client.transport.answers = {
        "which-segment": {
            "id": "which-segment",
            "type": "choice",
            "probabilities": {"Growth": 1.5, "Startup": 0.1, "Enterprise": 0.0},
        }
    }
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        assert asyncio.run(client.decide([question])) == []
    assert "1.5" in caplog.records[0].getMessage()


def test_malformed_payloads_dropped_with_warning(caplog):
    """Rule pin (payload shape): a non-dict payload and payloads missing
    id / type / probabilities are each dropped with a WARNING naming the
    question id and <missing> — nothing silent, nothing crashing decide."""
    questions = [
        decision.NoulQuestion(id="q1", prompt="Q1?"),
        decision.NoulQuestion(id="q2", prompt="Q2?"),
        decision.NoulQuestion(id="q3", prompt="Q3?"),
        decision.NoulQuestion(id="q4", prompt="Q4?"),
    ]
    answers_map = {
        "q1": None,  # not a dict at all
        "q2": {"type": "noul", "probabilities": 0.5},  # missing id
        "q3": {"id": "q3", "probabilities": 0.5},  # missing type
        "q4": {"id": "q4", "type": "noul"},  # missing probabilities
    }
    client = decision.DecisionClient(decision.MockTransport(answers=answers_map))
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        answers = asyncio.run(client.decide(questions))

    assert answers == []
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 4
    # Every drop names its question id; payloads without a readable id are
    # represented by the <missing> placeholder.
    for question_id, message in zip(("q1", "q2", "q3", "q4"), messages):
        assert question_id in message
    assert "<missing>" in messages[0] and "<missing>" in messages[1]
    assert "q3" in messages[2] and "q4" in messages[3]


def test_question_construction_is_fail_loud():
    """Schema pins: an empty id or prompt, a single option, duplicate
    options, a blank option and a non-list options value all raise
    ValueError at construction — answer dicts and argmax consumers are
    keyed by these fields, so bad shapes fail before any transport call;
    the `type` field defaults to the wire type name on each class."""
    with pytest.raises(ValueError):
        decision.NoulQuestion(id="", prompt="Clean?")
    with pytest.raises(ValueError):
        decision.ScoreQuestion(id="score-1", prompt="")
    with pytest.raises(ValueError):
        decision.ChoiceQuestion(id="c", prompt="Pick.", options=["only"])
    with pytest.raises(ValueError):
        decision.ChoiceQuestion(id="c", prompt="Pick.", options=["same", "same"])
    with pytest.raises(ValueError):
        decision.ChoiceQuestion(id="c", prompt="Pick.", options=["good", ""])
    with pytest.raises(ValueError):
        decision.ChoiceQuestion(id="c", prompt="Pick.", options="ab")

    # Happy paths: options normalize to a tuple, type defaults to the wire name.
    normalized = decision.ChoiceQuestion(id="c", prompt="Pick.", options=["a", "b"])
    assert normalized.options == ("a", "b") and normalized.type == "choice"
    assert decision.NoulQuestion(id="n", prompt="p").type == "noul"
    assert decision.ScoreQuestion(id="s", prompt="p").type == "score"
    top = decision.top_probability({"a": 0.3, "b": 0.9})
    assert top == 0.9 and decision.top_probability({}) == 0.0
    assert decision.top_probability(0.55) == 0.55


def test_decide_empty_batch_returns_empty_list():
    """Assumption pin: decide([]) returns [] without calling the transport —
    a MockTransport with no configured answer raises KeyError on ANY call,
    so returning [] proves the empty batch short-circuits."""
    client = decision.DecisionClient(decision.MockTransport())
    assert asyncio.run(client.decide([])) == []


def test_invalid_thresholds_fail_loud(monkeypatch):
    """Threshold policy: a non-numeric DECISION_THRESHOLD (abc / empty), one
    outside [0, 1] (7 / -0.1 / 1.5) and an explicit bool or out-of-range
    threshold argument all raise ValueError at construction — fail at
    wiring time, not mid-decide."""
    for bad in ("abc", "", "7", "-0.1", "1.5"):
        monkeypatch.setenv("DECISION_THRESHOLD", bad)
        with pytest.raises(ValueError):
            decision.DecisionClient(decision.MockTransport())
    for bad in (True, 1.5, -0.1, "high"):
        with pytest.raises(ValueError):
            decision.DecisionClient(decision.MockTransport(), threshold=bad)


def test_module_imports_both_styles():
    """Entry contract: the decision layer works both as `import
    agent.decision` from the repo root (package-style runtime probes) and
    as `import decision` from agent/ (tests), via the same dual import as
    quality.py / server.py — and both entries expose the re-exported
    public surface."""
    exports = (
        "Answer, ChatStructuredTransport, DecisionClient, "
        "DecisionsModalityTransport, DEFAULT_THRESHOLD, "
        "FallbackChainTransport, MAX_ATTEMPTS_PER_TRANSPORT, MockTransport, "
        "NoulQuestion, Question, ScoreQuestion, TransportError, "
        "build_decision_client, top_probability"
    )
    package_style = subprocess.run(
        [sys.executable, "-c", f"from agent.decision import {exports}"],
        cwd=AGENT_DIR.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert package_style.returncode == 0, package_style.stderr
    script_style = subprocess.run(
        [sys.executable, "-c", f"from decision import {exports}"],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert script_style.returncode == 0, script_style.stderr

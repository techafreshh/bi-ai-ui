"""Acceptance-contract tests for the OpenRouter decision transports (issue
#9).

One test per acceptance clause of issue #9, each with a docstring naming
the clause it proves, all over injected httpx.MockTransport clients — no
real API key, no network. Mirrors test_decision_client.py's house style:
asyncio.run entry, caplog on "bi-ai-ui.agent.decision", imports via the
script-style `decision` package.
"""

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

import decision
from decision.client import (
    DEFAULT_DECISION_ENDPOINT,
    DEFAULT_DECISION_MODEL,
    DEFAULT_FALLBACK_MODELS,
)

AGENT_DIR = Path(__file__).resolve().parents[1]
DECISION_LOGGER = "bi-ai-ui.agent.decision"

CATALOG_OPTIONS = [
    "kpi_row",
    "line_chart",
    "variance_callout",
    "data_table",
    "data_quality_card",
    "insight_text",
]


def _canonical_questions():
    """The spike's question set (acceptance clause 2 shapes): one choice
    question with the 6 catalog options plus one noul question."""
    return [
        decision.ChoiceQuestion(
            id="which-components",
            prompt="Which catalog components does this query need?",
            options=CATALOG_OPTIONS,
            multi=True,
        ),
        decision.NoulQuestion(
            id="is-data-problem",
            prompt="Does the evidence point to a data problem?",
        ),
    ]


CHOICE_PROBS = {
    "kpi_row": 0.43,
    "line_chart": 0.25,
    "variance_callout": 0.12,
    "data_table": 0.10,
    "data_quality_card": 0.06,
    "insight_text": 0.04,
}


def _canon_answers(choice_probs=None):
    """A valid alpha decisions body for the expanded canonical questions —
    answers keyed by wire id, no id fields. The catalog choice is six
    per-option nouls (alpha choice itself is mutually exclusive), and the
    data-problem noul is under `noul` (the live shape)."""
    probabilities = choice_probs or CHOICE_PROBS
    answers = {
        f"which-components::{option}": {"type": "noul", "noul": probability}
        for option, probability in probabilities.items()
    }
    answers["is-data-problem"] = {"type": "noul", "noul": 0.92}
    return {"answers": answers}


def _chat_answers():
    """The fallback chat transport's internal answers-array shape."""
    return {
        "answers": [
            {
                "id": "which-components",
                "type": "choice",
                "probabilities": CHOICE_PROBS,
            },
            {"id": "is-data-problem", "type": "noul", "probabilities": 0.92},
        ]
    }


def _chat_body(answers):
    """A chat-completions body whose message content JSON-encodes the given
    answers shape — what ChatStructuredTransport must extract and parse."""
    return {
        "choices": [
            {"message": {"content": json.dumps(answers)}},
        ]
    }


def _mock_client(handler):
    """The issue-mandated test seam: an httpx client whose transport is a
    MockTransport handler. Aclose is the test's job (the transport never
    owns an injected client)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_missing_api_key_fails_at_construction(monkeypatch):
    """Acceptance clause 4: with OPENROUTER_API_KEY unset (and empty), both
    transports and build_decision_client raise RuntimeError at construction
    naming the env var and .env.example — while the #8 MockTransport client
    still constructs, because DecisionClient itself stays key-agnostic."""
    for bad_env in ("delenv", "empty"):
        if bad_env == "delenv":
            monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        else:
            monkeypatch.setenv("OPENROUTER_API_KEY", "")
        with pytest.raises(RuntimeError) as exc:
            decision.DecisionsModalityTransport()
        assert "OPENROUTER_API_KEY" in str(exc.value)
        assert ".env.example" in str(exc.value)
        with pytest.raises(RuntimeError) as exc:
            decision.ChatStructuredTransport()
        assert "OPENROUTER_API_KEY" in str(exc.value)
        with pytest.raises(RuntimeError) as exc:
            decision.build_decision_client()
        assert "OPENROUTER_API_KEY" in str(exc.value)

    # The seam itself (#8 contract) must not require a key.
    decision.DecisionClient(decision.MockTransport())


def test_decisions_transport_sends_alpha_wire_format(monkeypatch):
    """Acceptance clause 1 (wire shape): the decisions transport POSTs the
    alpha batch body — bearer key, DECISION_MODEL model, one state snapshot,
    questions keyed by id, prompt under `instructions`, choice criteria as a
    label->description record — and normalizes the id-keyed answers body."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DECISION_MODEL", "stub/decider")
    monkeypatch.delenv("DECISION_TIMEOUT_S", raising=False)
    requests = []

    def handler(request):
        requests.append(
            {
                "path": request.url.path,
                "authorization": request.headers.get("Authorization"),
                "body": json.loads(request.content.decode()),
            }
        )
        return httpx.Response(200, json=_canon_answers())

    transport = decision.DecisionsModalityTransport(
        endpoint="http://decisions.test/api/alpha/decisions",
        state={"arr_trend": "up"},
        http_client=_mock_client(handler),
    )
    questions = _canonical_questions()
    mapping = asyncio.run(transport.ask_batch(questions))
    asyncio.run(transport.aclose())

    assert len(requests) == 1
    wire = requests[0]
    assert wire["path"] == "/api/alpha/decisions"
    assert wire["authorization"] == "Bearer test-key"
    assert wire["body"]["model"] == "stub/decider"
    assert wire["body"]["state"] == {"arr_trend": "up"}
    sent = wire["body"]["questions"]
    assert list(sent) == [
        *(f"which-components::{option}" for option in CATALOG_OPTIONS),
        "is-data-problem",
    ]
    for option in CATALOG_OPTIONS:
        expanded = sent[f"which-components::{option}"]
        assert expanded["type"] == "noul"
        assert "Which catalog components" in expanded["instructions"]
        assert option in expanded["instructions"]
        assert expanded["criteria"] == {
            "true": f"The answer needs {option}.",
            "false": f"The answer does not need {option}.",
        }
        assert "id" not in expanded and "options" not in expanded
    assert sent["is-data-problem"] == {
        "type": "noul",
        "instructions": "Does the evidence point to a data problem?",
    }
    assert set(mapping) == {"which-components", "is-data-problem"}
    assert mapping["which-components"]["probabilities"] == CHOICE_PROBS
    assert mapping["is-data-problem"]["probabilities"] == 0.92
    # parse sees the raw expanded wire ids; ask_batch is what folds them back.
    raw = decision.DecisionsModalityTransport.parse(_canon_answers())
    assert set(raw) == {
        *(f"which-components::{option}" for option in CATALOG_OPTIONS),
        "is-data-problem",
    }


def test_decision_endpoint_resolution_precedence(monkeypatch):
    """The decisions URL resolves at construction: DECISION_ENDPOINT wins,
    a non-default OPENROUTER_BASE_URL derives the legacy {base}/decisions
    path (fake-server seam), and otherwise the live alpha URL is used."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("DECISION_ENDPOINT", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    transport = decision.DecisionsModalityTransport()
    assert transport.endpoint == DEFAULT_DECISION_ENDPOINT
    asyncio.run(transport.aclose())

    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://fake.test/")
    transport = decision.DecisionsModalityTransport()
    assert transport.endpoint == "http://fake.test/decisions"
    asyncio.run(transport.aclose())

    monkeypatch.setenv("DECISION_ENDPOINT", "http://explicit.test/alpha/")
    transport = decision.DecisionsModalityTransport()
    assert transport.endpoint == "http://explicit.test/alpha"
    asyncio.run(transport.aclose())


def test_decisions_transport_retries_then_transport_error(monkeypatch):
    """Acceptance clause 3 (bounded): a bare decisions transport makes
    exactly ONE request per ask_batch and raises TransportError on 401; the
    chain retries it up to MAX_ATTEMPTS_PER_TRANSPORT=2 — exactly 2
    requests to the alpha endpoint, then TransportError, never more."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    hits = []

    def handler(request):
        hits.append(request.url.path)
        return httpx.Response(401, text="bad key")

    def make_transport():
        return decision.DecisionsModalityTransport(
            endpoint="http://decisions.test/api/alpha/decisions",
            http_client=_mock_client(handler),
        )

    with pytest.raises(decision.TransportError):
        asyncio.run(make_transport().ask_batch(_canonical_questions()))
    assert hits == ["/api/alpha/decisions"]

    hits.clear()
    chain = decision.FallbackChainTransport([make_transport()])
    with pytest.raises(decision.TransportError):
        asyncio.run(chain.ask_batch(_canonical_questions()))
    assert hits.count("/api/alpha/decisions") == 2
    assert decision.MAX_ATTEMPTS_PER_TRANSPORT == 2


def test_fallback_engages_on_decisions_failure(monkeypatch, caplog):
    """Acceptance clause 3 (fallback): with the decisions endpoint 401ing, a
    build_decision_client() decide() engages the chat structured-output
    fallback — decisions hit exactly twice (2 bounded attempts), the chat
    endpoint once, answers return in question order with choice
    probabilities summing to ~1.0, and a warning names
    DecisionsModalityTransport."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://fallback.test/")
    monkeypatch.setenv("DECISION_ENDPOINT", "http://fallback.test/decisions")
    monkeypatch.delenv("DECISION_MODEL", raising=False)
    monkeypatch.delenv("DECISION_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("DECISION_THRESHOLD", raising=False)
    hits = {"decisions": 0, "chat": 0}
    chat_requests = []

    def handler(request):
        path = request.url.path
        if path == "/decisions":
            hits["decisions"] += 1
            return httpx.Response(401, text="bad key")
        hits["chat"] += 1
        chat_requests.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_chat_body(_chat_answers()))

    client = decision.build_decision_client()
    # Factory wiring works when a key is present; for the HTTP contract the
    # chain is rebuilt over injected MockTransport clients (the test seam).
    asyncio.run(client.transport.aclose())
    client = decision.DecisionClient(
        decision.FallbackChainTransport(
            [
                decision.DecisionsModalityTransport(
                    http_client=_mock_client(handler)
                ),
                decision.ChatStructuredTransport(
                    models=["m-one", "m-two"],
                    http_client=_mock_client(handler),
                ),
            ]
        )
    )

    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        answers = asyncio.run(client.decide(_canonical_questions()))

    assert hits["decisions"] == 2
    assert hits["chat"] == 1
    assert [a.id for a in answers] == ["which-components", "is-data-problem"]
    choice_probs = answers[0].probabilities
    assert set(choice_probs) == set(CATALOG_OPTIONS)
    assert sum(choice_probs.values()) == pytest.approx(1.0, abs=0.05)
    assert answers[0].above_threshold is False  # top = 0.43 < 0.6
    assert answers[1].probabilities == pytest.approx(0.92)
    assert answers[1].above_threshold is True
    # The structured-output request shape: json_schema, per the contract.
    assert chat_requests[0]["response_format"]["type"] == "json_schema"
    assert (
        chat_requests[0]["response_format"]["json_schema"]["name"]
        == "decision_answers"
    )
    warnings_text = " | ".join(
        record.getMessage() for record in caplog.records
    )
    assert "DecisionsModalityTransport" in warnings_text
    asyncio.run(client.transport.aclose())


def test_request_timeout_triggers_bounded_fallback(monkeypatch, caplog):
    """Issue #20 (timeout drops): a request that hits the transport-level
    DECISION_TIMEOUT_S timeout — httpx.ReadTimeout on /decisions — is
    wrapped into TransportError ("decisions endpoint request failed"),
    retried exactly MAX_ATTEMPTS_PER_TRANSPORT (2) times on the primary,
    then the chat fallback answers. The timeout is raised as an exception,
    never a real sleep, so the test carries zero wall-clock sensitivity."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    hits = {"decisions": 0, "chat": 0}

    def handler(request):
        if request.url.path == "/decisions":
            hits["decisions"] += 1
            raise httpx.ReadTimeout("mock read timeout")
        hits["chat"] += 1
        return httpx.Response(200, json=_chat_body(_chat_answers()))

    client = decision.DecisionClient(
        decision.FallbackChainTransport(
            [
                decision.DecisionsModalityTransport(
                    endpoint="http://timeout.test/decisions",
                    http_client=_mock_client(handler),
                ),
                decision.ChatStructuredTransport(
                    models=["m-good"],
                    base_url="http://timeout.test",
                    http_client=_mock_client(handler),
                ),
            ]
        )
    )

    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        answers = asyncio.run(client.decide(_canonical_questions()))

    # 2 bounded attempts against the timing-out endpoint, then one chat call.
    assert hits == {"decisions": 2, "chat": 1}
    assert [a.id for a in answers] == ["which-components", "is-data-problem"]
    assert [a.type for a in answers] == ["choice", "noul"]
    assert answers[0].above_threshold is False  # top canon choice prob 0.43
    assert answers[1].probabilities == pytest.approx(0.92)
    warnings_text = " | ".join(
        record.getMessage() for record in caplog.records
    )
    # Every attempt's timeout is wrapped and warned, naming the endpoint.
    assert "decisions endpoint request failed" in warnings_text
    assert "DecisionsModalityTransport" in warnings_text
    asyncio.run(client.transport.aclose())


def test_chat_transport_walks_model_chain_bounded(monkeypatch):
    """Acceptance clause 3 (no infinite retries): with every endpoint 401ing,
    the chain exhausts after exactly 2 decisions + 4 chat requests (2 models
    x 2 chain attempts) and raises a TransportError summarizing BOTH
    transports' last failure."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    hits = {"decisions": 0, "chat": 0}

    def handler(request):
        if request.url.path == "/api/alpha/decisions":
            hits["decisions"] += 1
        else:
            hits["chat"] += 1
        return httpx.Response(401, text="bad key")

    chain = decision.FallbackChainTransport(
        [
            decision.DecisionsModalityTransport(
                endpoint="http://chain.test/api/alpha/decisions",
                http_client=_mock_client(handler),
            ),
            decision.ChatStructuredTransport(
                models=["m-one", "m-two"],
                base_url="http://chain.test",
                http_client=_mock_client(handler),
            ),
        ]
    )
    with pytest.raises(decision.TransportError) as exc:
        asyncio.run(chain.ask_batch(_canonical_questions()))

    assert hits == {"decisions": 2, "chat": 4}
    message = str(exc.value)
    assert "DecisionsModalityTransport" in message
    assert "ChatStructuredTransport" in message
    asyncio.run(chain.aclose())


def test_chat_transport_rejects_unusable_content(monkeypatch, caplog):
    """Acceptance clause 3 (content shape): a 200 chat response whose message
    content is not JSON raises (as a warning) a model-naming failure inside
    ChatStructuredTransport, which then falls through to the next model and
    still returns its answers."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    chat_hits = []

    def handler(request):
        chat_hits.append(json.loads(request.content.decode())["model"])
        if chat_hits[-1] == "m-bad":
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "not json at all"}}]}
            )
        return httpx.Response(200, json=_chat_body(_chat_answers()))

    transport = decision.ChatStructuredTransport(
        models=["m-bad", "m-good"],
        base_url="http://chat.test",
        http_client=_mock_client(handler),
    )
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        mapping = asyncio.run(transport.ask_batch(_canonical_questions()))

    assert chat_hits == ["m-bad", "m-good"]
    assert set(mapping) == {"which-components", "is-data-problem"}
    warnings_text = " | ".join(record.getMessage() for record in caplog.records)
    assert "m-bad" in warnings_text and "not JSON" in warnings_text
    asyncio.run(transport.aclose())


def test_decide_accepts_batch_transport(caplog):
    """Batch seam: a transport with ONLY ask_batch gets exactly one call
    carrying the full question list in order; question ids absent from the
    returned mapping are warned once with the rule-table <missing> drop
    message and parsed as dropped, while answered questions keep order."""
    answered = {
        "q1": {"id": "q1", "type": "noul", "probabilities": 0.9},
        "q3": {"id": "q3", "type": "noul", "probabilities": 0.7},
    }

    class StubBatchTransport:
        def __init__(self):
            self.calls = []

        async def ask_batch(self, questions):
            self.calls.append([q.id for q in questions])
            return answered

    stub = StubBatchTransport()
    questions = [
        decision.NoulQuestion(id="q1", prompt="one?"),
        decision.NoulQuestion(id="q2", prompt="two?"),
        decision.NoulQuestion(id="q3", prompt="three?"),
    ]
    client = decision.DecisionClient(stub)
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        answers = asyncio.run(client.decide(questions))

    assert stub.calls == [["q1", "q2", "q3"]]
    assert [a.id for a in answers] == ["q1", "q3"]
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "q2" in messages[0] and "<missing>" in messages[0]


def test_parse_contract_snapshot_yields_answers_for_every_question_id(monkeypatch):
    """Acceptance clause 1: the committed contract snapshot — the spike's
    {"meta", "response"} capture of the real alpha decisions wire body —
    parses via DecisionsModalityTransport.parse (no key, no instance) into
    one answer per expanded question id, with [0,1]-bounded probabilities:
    a noul per catalog option (the multi-select fan-out), the data-problem
    noul, and the normalized 0-1 score."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DECISION_MODEL", raising=False)
    snapshot = json.loads(
        (AGENT_DIR / "tests" / "contract_snapshot.json").read_text(encoding="utf-8")
    )
    mapping = decision.DecisionsModalityTransport.parse(snapshot)

    assert set(mapping) == {
        *(f"which-components::{option}" for option in CATALOG_OPTIONS),
        "is-data-problem",
        "dq-confidence",
    }
    for option in CATALOG_OPTIONS:
        probability = mapping[f"which-components::{option}"]["probabilities"]
        assert not isinstance(probability, bool)
        assert 0.0 <= probability <= 1.0
    noul = mapping["is-data-problem"]["probabilities"]
    assert not isinstance(noul, bool) and isinstance(noul, (int, float))
    assert 0.0 <= noul <= 1.0
    score = mapping["dq-confidence"]["probabilities"]
    assert not isinstance(score, bool) and isinstance(score, (int, float))
    assert 0.0 <= score <= 1.0


def test_alpha_score_is_normalized_and_never_clamped(caplog):
    """Alpha score normalization: a continuous criteria index folds to the
    internal [0,1] scalar, while an out-of-range index (3.0 on a 3-rung
    scale -> 1.5) is dropped whole, never clamped."""
    question = decision.ScoreQuestion(id="momentum", prompt="rate?")
    raw = {
        "answers": {
            "momentum": {
                "type": "score",
                "score": 1.0057,
                "legend": {"0": "low", "1": "mid", "2": "high"},
                "probabilities": {"0": 0.3, "1": 0.4, "2": 0.3},
            }
        }
    }
    mapping = decision.DecisionsModalityTransport.parse(raw)
    assert mapping["momentum"]["probabilities"] == pytest.approx(1.0057 / 2)

    raw["answers"]["momentum"]["score"] = 3.0
    mapping = decision.DecisionsModalityTransport.parse(raw)
    client = decision.DecisionClient(decision.MockTransport(answers=mapping))
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        assert asyncio.run(client.decide([question])) == []
    assert "1.5" in caplog.records[0].getMessage()


# --- Issue #19: per-type model overrides and the TypeRoutedTransport ---------


def _router_questions():
    """One question per type (choice + an extra choice to prove partition
    batching) for the TypeRoutedTransport tests."""
    return [
        decision.ChoiceQuestion(id="q-choice", prompt="pick?", options=["a", "b"]),
        decision.NoulQuestion(id="q-noul", prompt="yes?"),
        decision.ScoreQuestion(id="q-score", prompt="rate?"),
        decision.ChoiceQuestion(id="q-choice-two", prompt="pick again?", options=["a", "b"]),
    ]


def _answers_for(questions):
    """Alpha-shaped answers echoing each id-keyed request question (choice
    probabilities, noul p(yes), score index + legend) — what the mock alpha
    endpoint replies no matter which type partition reached it."""
    answers = {}
    for question_id, question in questions.items():
        if question["type"] == "choice":
            answers[question_id] = {
                "type": "choice",
                "choice": next(iter(question["criteria"])),
                "probabilities": {
                    option: 0.9 for option in question["criteria"]
                },
            }
        elif question["type"] == "noul":
            answers[question_id] = {"type": "noul", "noul": 0.9}
        else:
            criteria = question["criteria"]
            answers[question_id] = {
                "type": "score",
                "score": 1.0,
                "legend": {
                    str(index): label for index, label in enumerate(criteria)
                },
                "probabilities": {
                    str(index): 0.5 for index in range(len(criteria))
                },
            }
    return {"answers": answers}


def test_per_type_model_reads_override_env(monkeypatch):
    """Issue #19: per_type_model returns the stripped override string per
    type, reads None for unset, empty or whitespace-only values (an emptied
    .env line degrades to DECISION_MODEL, never wires a broken model string)
    and None for unknown question types."""
    for var in ("DECISION_MODEL_CHOICE", "DECISION_MODEL_NOUL", "DECISION_MODEL_SCORE"):
        monkeypatch.delenv(var, raising=False)
    assert decision.per_type_model("choice") is None
    assert decision.per_type_model("noul") is None
    assert decision.per_type_model("score") is None
    assert decision.per_type_model("brand_new_kind") is None

    monkeypatch.setenv("DECISION_MODEL_CHOICE", "  override/model  ")
    assert decision.per_type_model("choice") == "override/model"
    monkeypatch.setenv("DECISION_MODEL_CHOICE", "   ")
    assert decision.per_type_model("choice") is None
    monkeypatch.setenv("DECISION_MODEL_CHOICE", "")
    assert decision.per_type_model("choice") is None


def test_build_decision_client_plain_wiring_is_structurally_unchanged(monkeypatch):
    """Issue #19 (byte-identical no-override path): with all three per-type
    overrides unset, build_decision_client returns the exact #9 wiring — a
    FallbackChainTransport of DecisionsModalityTransport +
    ChatStructuredTransport, no router in between."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("DECISION_ENDPOINT", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("DECISION_MODEL", raising=False)
    monkeypatch.delenv("DECISION_FALLBACK_MODELS", raising=False)
    for var in ("DECISION_MODEL_CHOICE", "DECISION_MODEL_NOUL", "DECISION_MODEL_SCORE"):
        monkeypatch.delenv(var, raising=False)

    client = decision.build_decision_client()
    assert isinstance(client.transport, decision.FallbackChainTransport)
    primary, fallback = client.transport.transports
    assert isinstance(primary, decision.DecisionsModalityTransport)
    assert primary.model == DEFAULT_DECISION_MODEL
    assert primary.endpoint == DEFAULT_DECISION_ENDPOINT
    assert isinstance(fallback, decision.ChatStructuredTransport)
    asyncio.run(client.transport.aclose())


def test_build_decision_client_wires_per_type_routes(monkeypatch):
    """Issue #19 (factory wiring): each SET override gets its own fallback
    chain whose primary decisions transport carries that model string,
    routed under a TypeRoutedTransport whose default stays the plain
    DECISION_MODEL chain; the chat fallback chain still walks
    DECISION_FALLBACK_MODELS for every route."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("DECISION_MODEL", raising=False)
    monkeypatch.delenv("DECISION_FALLBACK_MODELS", raising=False)
    monkeypatch.setenv("DECISION_MODEL_CHOICE", "x/y")
    monkeypatch.setenv("DECISION_MODEL_NOUL", "")  # empty counts as unset
    monkeypatch.setenv("DECISION_MODEL_SCORE", "z/w")

    client = decision.build_decision_client()
    router = client.transport
    assert isinstance(router, decision.TypeRoutedTransport)
    assert set(router.routes) == {"choice", "score"}
    for question_type, model in (("choice", "x/y"), ("score", "z/w")):
        chain = router.routes[question_type]
        assert isinstance(chain, decision.FallbackChainTransport), question_type
        assert chain.transports[0].model == model, question_type
        assert isinstance(chain.transports[1], decision.ChatStructuredTransport)
        assert chain.transports[1].models == list(DEFAULT_FALLBACK_MODELS)
    # noul had no (usable) override: it falls to the plain default chain.
    default_chain = router.default
    assert isinstance(default_chain, decision.FallbackChainTransport)
    assert default_chain.transports[0].model == DEFAULT_DECISION_MODEL
    asyncio.run(client.transport.aclose())


def test_build_decision_client_requires_key_for_router_too(monkeypatch):
    """Issue #19 (wiring policy unchanged): per-type overrides do not bypass
    the fail-loud missing-key rule — a set override with no key still raises
    RuntimeError at construction."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("DECISION_MODEL_CHOICE", "x/y")
    with pytest.raises(RuntimeError) as exc:
        decision.build_decision_client()
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_type_routed_transport_partitions_by_type_and_merges(monkeypatch):
    """Issue #19 (runtime behavior): one ask_batch over a mixed
    choice/noul/score set is partitioned by type — the choice partition's
    /decisions wire body carries the override model "x/y", score's carries
    "z/w", the unrouted noul partition carries the default's model — each
    partition is one HTTP call, and the merged mapping answers every
    question in the original order through DecisionClient.decide."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    requests = []

    def handler(request):
        body = json.loads(request.content.decode())
        requests.append(
            (request.url.path, body["model"], list(body["questions"]))
        )
        return httpx.Response(200, json=_answers_for(body["questions"]))

    def make(model):
        return decision.DecisionsModalityTransport(
            model=model,
            endpoint="http://route.test/api/alpha/decisions",
            http_client=_mock_client(handler),
        )

    questions = _router_questions()
    router = decision.TypeRoutedTransport(
        {"choice": make("x/y"), "score": make("z/w")},
        make("stub/default"),
    )
    answers = asyncio.run(
        decision.DecisionClient(router, threshold=0.6).decide(questions)
    )

    # One request per partition, in first-question order: choice first, then
    # the unrouted noul partition (default), then score.
    assert requests == [
        ("/api/alpha/decisions", "x/y", ["q-choice", "q-choice-two"]),
        ("/api/alpha/decisions", "stub/default", ["q-noul"]),
        ("/api/alpha/decisions", "z/w", ["q-score"]),
    ]
    assert [a.id for a in answers] == ["q-choice", "q-noul", "q-score", "q-choice-two"]
    assert answers[0].probabilities == {"a": 0.9, "b": 0.9}
    assert answers[1].probabilities == pytest.approx(0.9)
    assert answers[2].probabilities == pytest.approx(0.5)  # index 1 / 2
    assert answers[3].above_threshold is True


def test_type_routed_transport_closes_every_subtransport():
    """Issue #19 (resource contract): aclose() closes the routed transports
    AND the default, mirroring FallbackChainTransport — no leaked clients
    when the graph rewires."""

    class ClosingBatch:
        def __init__(self):
            self.closed = False

        async def ask_batch(self, questions):
            return {}

        async def aclose(self):
            self.closed = True

    routed, default = ClosingBatch(), ClosingBatch()
    router = decision.TypeRoutedTransport({"choice": routed}, default)
    asyncio.run(router.aclose())
    assert routed.closed and default.closed


def test_type_routed_transport_requires_routes_and_default():
    """Issue #19 (fail-loud wiring): an empty routes mapping or a default-less
    router raises ValueError at construction — coverage gaps are wiring bugs,
    never silent per-request surprises."""

    class Anything:
        async def ask_batch(self, questions):
            return {}

    with pytest.raises(ValueError):
        decision.TypeRoutedTransport({}, Anything())
    with pytest.raises(ValueError):
        decision.TypeRoutedTransport({"choice": Anything()}, None)

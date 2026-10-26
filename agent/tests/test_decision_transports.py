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
        ),
        decision.NoulQuestion(
            id="is-data-problem",
            prompt="Does the evidence point to a data problem?",
        ),
    ]


def _canon_answers(choice_probs=None):
    """A valid PRD §10 answers body for the canonical questions."""
    return {
        "answers": [
            {
                "id": "which-components",
                "type": "choice",
                "probabilities": choice_probs
                or {
                    "kpi_row": 0.43,
                    "line_chart": 0.25,
                    "variance_callout": 0.12,
                    "data_table": 0.10,
                    "data_quality_card": 0.06,
                    "insight_text": 0.04,
                },
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


def test_decisions_transport_sends_prd_wire_format(monkeypatch):
    """Acceptance clause 1 (wire shape): the decisions transport POSTs the
    PRD §10 batch body — bearer key, DECISION_MODEL model, one state
    snapshot, options only on the choice question — and returns (and parse
    re-derives) the id -> raw payload mapping from the {"answers": [...]}
    body."""
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
        base_url="http://decisions.test",
        state={"arr_trend": "up"},
        http_client=_mock_client(handler),
    )
    questions = _canonical_questions()
    mapping = asyncio.run(transport.ask_batch(questions))
    asyncio.run(transport.aclose())

    assert len(requests) == 1
    wire = requests[0]
    assert wire["path"] == "/decisions"
    assert wire["authorization"] == "Bearer test-key"
    assert wire["body"]["model"] == "stub/decider"
    assert wire["body"]["state"] == {"arr_trend": "up"}
    sent = wire["body"]["questions"]
    assert [q["id"] for q in sent] == ["which-components", "is-data-problem"]
    # options only on the choice question (PRD §10 wire format)
    assert sent[0]["options"] == CATALOG_OPTIONS
    assert "options" not in sent[1]
    assert set(mapping) == {"which-components", "is-data-problem"}
    assert mapping["is-data-problem"]["probabilities"] == 0.92
    # parse over the raw body (no transport instance state needed) matches.
    assert decision.DecisionsModalityTransport.parse(_canon_answers()) == mapping


def test_decisions_transport_retries_then_transport_error(monkeypatch):
    """Acceptance clause 3 (bounded): a bare decisions transport makes
    exactly ONE request per ask_batch and raises TransportError on 401; the
    chain retries it up to MAX_ATTEMPTS_PER_TRANSPORT=2 — exactly 2
    requests to /decisions, then TransportError, never more."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    hits = []

    def handler(request):
        hits.append(request.url.path)
        return httpx.Response(401, text="bad key")

    def make_transport():
        return decision.DecisionsModalityTransport(
            base_url="http://decisions.test",
            http_client=_mock_client(handler),
        )

    with pytest.raises(decision.TransportError):
        asyncio.run(make_transport().ask_batch(_canonical_questions()))
    assert hits == ["/decisions"]

    hits.clear()
    chain = decision.FallbackChainTransport([make_transport()])
    with pytest.raises(decision.TransportError):
        asyncio.run(chain.ask_batch(_canonical_questions()))
    assert hits.count("/decisions") == 2
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
        return httpx.Response(200, json=_chat_body(_canon_answers()))

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


def test_chat_transport_walks_model_chain_bounded(monkeypatch):
    """Acceptance clause 3 (no infinite retries): with every endpoint 401ing,
    the chain exhausts after exactly 2 decisions + 4 chat requests (2 models
    x 2 chain attempts) and raises a TransportError summarizing BOTH
    transports' last failure."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    hits = {"decisions": 0, "chat": 0}

    def handler(request):
        if request.url.path == "/decisions":
            hits["decisions"] += 1
        else:
            hits["chat"] += 1
        return httpx.Response(401, text="bad key")

    chain = decision.FallbackChainTransport(
        [
            decision.DecisionsModalityTransport(
                base_url="http://chain.test", http_client=_mock_client(handler)
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
        return httpx.Response(200, json=_chat_body(_canon_answers()))

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
    {"meta", "response"} capture of the real decisions wire body — parses
    via DecisionsModalityTransport.parse (no key, no instance) into answers
    for the two canonical question ids, with [0,1]-bounded probabilities:
    a dict over catalog options for the choice, a float for the noul."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DECISION_MODEL", raising=False)
    snapshot = json.loads(
        (AGENT_DIR / "tests" / "contract_snapshot.json").read_text(encoding="utf-8")
    )
    mapping = decision.DecisionsModalityTransport.parse(snapshot)

    assert set(mapping) == {"which-components", "is-data-problem"}
    choice = mapping["which-components"]["probabilities"]
    assert isinstance(choice, dict)
    assert set(choice) <= set(CATALOG_OPTIONS)
    assert choice and all(
        not isinstance(v, bool) and 0.0 <= v <= 1.0 for v in choice.values()
    )
    noul = mapping["is-data-problem"]["probabilities"]
    assert not isinstance(noul, bool) and isinstance(noul, (int, float))
    assert 0.0 <= noul <= 1.0

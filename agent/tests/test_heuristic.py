"""Contract tests for HeuristicTransport, the decide node's key-free
fallback transport (issue #10).

The transport IS the no-key decider, the on-error fallback and the
deterministic stand-in for the runtime gate, so per golden query it must
endorse exactly its route's components above the 0.6 default threshold,
keep every other option at 0.0 (the layout cut then drops them), and
answer every question kind with a parseable payload without ever raising.
"""

import asyncio

import pytest

import questions as questions_module
from decision.heuristic import HeuristicTransport, classify, quoted_prompt

TRANSPORT = HeuristicTransport()

GOLDEN_QUERIES = {
    "Show me sales in Q3": {"kpi_row": 0.9, "line_chart": 0.85},
    "Why did churn spike after the pricing change?": {
        "variance_callout": 0.88,
        "data_table": 0.82,
    },
    "Is the data clean? Check for duplicates and nulls": {
        "data_quality_card": 0.95,
    },
}


def _question_set(text):
    return questions_module.build_question_set(text, {})


def _ask(question):
    return asyncio.run(TRANSPORT.ask(question))


@pytest.mark.parametrize("query,endorsed", sorted(GOLDEN_QUERIES.items()))
def test_golden_query_endorses_its_route(query, endorsed):
    """Each golden query's choice answer sits exactly on its route: the
    endorsed components carry their canned above-threshold probabilities,
    everything else reads 0.0 (unendorsed — dropped by the layout cut)."""
    choice = next(
        q for q in _question_set(query) if q.id == questions_module.CATALOG_QUESTION_ID
    )
    payload = _ask(choice)
    assert payload["id"] == choice.id
    assert payload["type"] == "choice"
    probabilities = payload["probabilities"]
    assert set(probabilities) == set(questions_module.CATALOG_TYPES)
    for option, probability in probabilities.items():
        if option in endorsed:
            assert probabilities[option] == pytest.approx(endorsed[option])
        else:
            assert probability == 0.0, option
        assert 0.0 <= probability <= 1.0


def test_off_catalog_query_scores_every_option_below_threshold():
    """Anything else routes to off-catalog: all six options at 0.0, so the
    threshold cut empties the canvas into the default insight view."""
    choice = next(
        q
        for q in _question_set("Tell me a joke about pirates")
        if q.id == questions_module.CATALOG_QUESTION_ID
    )
    payload = _ask(choice)
    assert set(payload["probabilities"].values()) == {0.0}


def test_noul_and_score_answers_are_canned_per_route():
    """Scalar questions answer with floats in [0, 1] — p(yes) for noul,
    the score for score — varying by route, never raising."""
    for query in ("Show me sales in Q3", "Is the data clean?", "random greeting"):
        for question in _question_set(query):
            if question.type == "choice":
                continue
            payload = _ask(question)
            assert payload["id"] == question.id
            assert payload["type"] == question.type
            value = payload["probabilities"]
            assert isinstance(value, float) and not isinstance(value, bool)
            assert 0.0 <= value <= 1.0


def test_unknown_question_kinds_stay_total_and_parseable():
    """A future question kind (not choice/noul/score semantics the tables
    know) still gets a total, in-[0, 1] answer instead of an exception."""
    from decision.schemas import ScoreQuestion

    stranger = ScoreQuestion(id="future_metric", prompt='Query: "Show me sales in Q3"')
    payload = _ask(stranger)
    assert isinstance(payload["probabilities"], float)
    assert 0.0 <= payload["probabilities"] <= 1.0


def test_classification_reads_the_quoted_message():
    """Route classification keys on the FIRST quoted span of the prompt —
    where build_question_set embeds the user message — with the whole
    prompt as fallback when no quotes exist."""
    assert quoted_prompt('The user asked: "Show me churn" is great') == "Show me churn"
    assert quoted_prompt("no quotes here at all") == "no quotes here at all"
    assert classify("show me CHURN drivers") == "churn"
    assert classify("is the data quality ok?") == "quality"
    assert classify("anything happens") == "off_catalog"


def test_ask_never_raises_on_hostile_prompts():
    """The fallback transport must be unbreakable: prompts without quotes,
    with broken quoting or nonsense all classify to a route and return
    parseable payloads (empty prompts cannot exist — the question schemas
    reject them — so they are not part of the seam)."""
    from decision.schemas import ChoiceQuestion, NoulQuestion

    hostile = [
        ChoiceQuestion(id="c", prompt="just words, no quotes at all", options=["a", "b"]),
        NoulQuestion(id="n", prompt='"unclosed "quotes" everywhere"'),
        ChoiceQuestion(id="c2", prompt="Show me sales in Q3", options=["a", "b"]),
    ]
    for question in hostile:
        payload = _ask(question)
        assert payload["id"] == question.id


def test_data_problem_noul_carries_the_banner_gate_probability():
    """Issue #17: the data_problem noul's p(yes) IS the data-problem
    probability — the value decisions[] ships and the DQ card's banner
    gates on — so each heuristic route's canned value must sit exactly
    where the golden paths need it: quality ≈0.94 (the PRD's ≈0.94, above
    the 0.7 decision clause and the 0.6 banner gate), the real-change
    routes low (a priced churn spike or a sales ramp is not a data
    problem), off-catalog neutral."""
    expected = {
        "Is the data clean? Check for duplicates and nulls": ("quality", 0.94),
        "Why did churn spike after the pricing change?": ("churn", 0.06),
        "Show me sales in Q3": ("sales", 0.06),
        "Tell me a joke about pirates": ("off_catalog", 0.25),
    }
    for query, (route, probability) in expected.items():
        question = next(
            q
            for q in _question_set(query)
            if q.id == questions_module.DATA_PROBLEM_QUESTION_ID
        )
        payload = _ask(question)
        assert payload["id"] == questions_module.DATA_PROBLEM_QUESTION_ID
        assert payload["type"] == "noul"
        value = payload["probabilities"]
        assert isinstance(value, float) and not isinstance(value, bool)
        assert value == probability, (query, route, value)

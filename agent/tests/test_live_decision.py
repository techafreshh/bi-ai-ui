"""Opt-in live contract probe for the decision chain (issue #20 notes).

The default suite run is key-free and network-free: this module SKIPS unless
LIVE=1 is exported, keeping the repo test command deterministic (the issue
pins the opt-in to exactly that env var). With LIVE=1 and a real
OPENROUTER_API_KEY in the environment, the probe builds the production
wiring through build_decision_client() — the real OpenRouter
decisions-modality primary plus its chat structured-output fallback chain —
and decides the contract spike's two canonical questions
(agent/scripts/contract_spike.py: the PRD §7 six-option catalog choice plus
one noul), asserting one typed Answer per question id in order. Answer
SHAPES are asserted, never exact probabilities: a live model owns the
values; the wire contract is what must hold.

Without a key the probe fails loud — build_decision_client() raises its
construction-time RuntimeError naming OPENROUTER_API_KEY — consistent with
the agent's fail-loud key contract: a LIVE run with a broken configuration
must not masquerade as a green skip. Run it with:

    LIVE=1 OPENROUTER_API_KEY=... pytest agent/tests -q -k live_decision
"""

import asyncio
import os

import pytest

import decision

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE") != "1",
    reason=(
        "live probe is opt-in: run with LIVE=1 and OPENROUTER_API_KEY set "
        "(needs network)"
    ),
)

# PRD §7 catalog — the choice question's options are exactly these 6 types
# (agent/scripts/contract_spike.py's canonical set).
CATALOG_OPTIONS = [
    "kpi_row",
    "line_chart",
    "variance_callout",
    "data_table",
    "data_quality_card",
    "insight_text",
]


def _canonical_questions():
    """The spike's two-question set: the 'which components' choice over the
    full 6-type catalog, plus one noul (yes/no) question."""
    return [
        decision.ChoiceQuestion(
            id="which-components",
            prompt="Which catalog components does this query need?",
            options=CATALOG_OPTIONS,
        ),
        decision.NoulQuestion(
            id="is-data-problem",
            prompt=(
                "Does the evidence point to a data problem (nulls, gaps, "
                "duplicates) rather than a business trend?"
            ),
        ),
    ]


def test_live_decision_chain_answers_canonical_questions():
    """LIVE=1: the production decision chain (build_decision_client wiring)
    answers the spike's two canonical questions — one typed Answer per
    question id, in order, with contract-shaped probabilities: a dict over
    catalog options for the choice, a single float in [0, 1] for the noul."""
    client = decision.build_decision_client()  # fails loud without a key
    try:
        answers = asyncio.run(client.decide(_canonical_questions()))
    finally:
        asyncio.run(client.transport.aclose())

    assert [a.id for a in answers] == ["which-components", "is-data-problem"]
    assert [a.type for a in answers] == ["choice", "noul"]
    choice, noul = answers
    assert isinstance(choice.probabilities, dict)
    assert choice.probabilities
    assert set(choice.probabilities) <= set(CATALOG_OPTIONS)
    assert all(
        not isinstance(v, bool) and 0.0 <= v <= 1.0
        for v in choice.probabilities.values()
    )
    assert not isinstance(noul.probabilities, bool)
    assert 0.0 <= noul.probabilities <= 1.0

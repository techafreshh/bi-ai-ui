"""HeuristicTransport: a deterministic, key-free decision transport.

It answers each question through the #8 per-question seam
(`async def ask(question) -> dict`) by keyword-classifying the quoted user
message that questions.build_question_set embeds in every prompt: the
first quoted span is lowercased and matched against three route tables —
quality words (clean/quality/duplicate/null), churn words
(churn/cancel/pricing), sales words (sales/revenue/show) — first match
wins; anything else is the off-catalog route whose every option scores
0.0. Canned per-route probabilities put exactly the route's components
above the 0.6 default threshold (sales -> kpi_row/line_chart, churn ->
variance_callout/data_table, quality -> data_quality_card), mirroring the
golden queries issue #10's tests and the runtime gate exercise.

Three roles, one implementation: the no-OPENROUTER-API_KEY decider (the
README's "no key still boots" contract), the decide node's on-error
fallback (any transport failure degrades to this, so `decisions` always
carries 1..7 entries), and the deterministic stand-in tests and the
runtime gate use when the live decision endpoint is unreachable. It never
raises and never inspects state beyond the prompt text — same dual-import
contract as the rest of the decision package.

`model = "heuristic"` is declared so the decide node can attribute answers
truthfully instead of guessing the chain's model label — and so
`top_probability` comparisons stay consistent with the canned values.
"""

import re

# First quoted span of the prompt — where build_question_set embeds the
# verbatim user message. Prompts without quotes fall back to the full text.
_QUOTED_RE = re.compile(r'"([^"]*)"')

# Route keyword tables, checked in order: the specific nouns first, the
# generic "show" last, so "show data quality issues" routes to quality even
# though it contains "show". Substring matching, so inflections
# ("cancellations", "nulls") match by their stem.
_ROUTES = (
    ("quality", ("clean", "quality", "duplicate", "null", "missing")),
    ("churn", ("churn", "cancel", "pricing", "price")),
    ("sales", ("sales", "revenue", "show")),
)

# Choice probabilities per route: exactly the route's components sit above
# the default 0.6 threshold, everything else reads 0.0 (unendorsed: the
# layout node's threshold cut drops it, and the off-catalog route's all-zero
# map is what empties the canvas into the fallback insight view).
_ROUTE_CHOICE = {
    "quality": {"data_quality_card": 0.95},
    "churn": {"variance_callout": 0.88, "data_table": 0.82},
    "sales": {"kpi_row": 0.9, "line_chart": 0.85},
    "off_catalog": {},
}

# Noul p(yes) per route, keyed by question id with a neutral default for
# ids this transport does not know (kept total so a future question kind
# still gets a parseable in-[0, 1] answer instead of an exception).
# Issue #17: the data_problem noul's p(yes) IS the data-problem probability
# (the DQ card's banner gate reads it from decisions[] directly) — the
# quality route scores 0.94 (the PRD's ≈0.94 data-problem reading for the
# clean-data routes it inverts) while the real-change routes score low.
_ROUTE_NOUL = {
    "data_problem": {
        "quality": 0.94,
        "churn": 0.06,
        "sales": 0.06,
        "off_catalog": 0.25,
    },
    "anomaly_callout": {
        "quality": 0.20,
        "churn": 0.80,
        "sales": 0.55,
        "off_catalog": 0.20,
    },
}

# Score answers per route, same totality contract as _ROUTE_NOUL.
_ROUTE_SCORE = {
    "dq_confidence": {
        "quality": 0.25,
        "churn": 0.60,
        "sales": 0.70,
        "off_catalog": 0.50,
    },
}


def classify(text):
    """Route a (lowercased) message through the keyword tables; the first
    matching route wins, no match is the off-catalog route."""
    lowered = (text or "").lower()
    for route, keywords in _ROUTES:
        if any(keyword in lowered for keyword in keywords):
            return route
    return "off_catalog"


def quoted_prompt(prompt):
    """The user message as questions.build_question_set embedded it: the
    prompt's first quoted span, or the whole prompt when it carries none."""
    match = _QUOTED_RE.search(prompt or "")
    return match.group(1) if match else (prompt or "")


class HeuristicTransport:
    """Keyword-classifying per-question transport (module docstring).

    model is declared ("heuristic") so answer attribution never has to
    guess; probabilities equal top_probability, so above_threshold flagging
    stays consistent with the canned values.
    """

    model = "heuristic"

    async def ask(self, question):
        """Classify the prompt's quoted message and return the raw payload
        dict DecisionClient parses — id/type echoed from the question, a
        total option->probability map for choice, a [0, 1] float for
        noul/score. Never raises: classification of any text lands in one
        of the four routes by construction."""
        route = classify(quoted_prompt(question.prompt))
        if question.type == "choice":
            probabilities = {option: 0.0 for option in question.options}
            for option, probability in _ROUTE_CHOICE.get(route, {}).items():
                if option in probabilities:
                    probabilities[option] = probability
        elif question.type == "score":
            probabilities = _ROUTE_SCORE.get(question.id, {}).get(route, 0.5)
        else:
            # noul (and any future scalar question kind): p(yes) per route.
            probabilities = _ROUTE_NOUL.get(question.id, {}).get(route, 0.5)
        return {
            "id": question.id,
            "type": question.type,
            "probabilities": probabilities,
        }

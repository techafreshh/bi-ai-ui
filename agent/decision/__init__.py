"""Typed probabilistic decision layer (issue #8): the question/answer
schemas, the DecisionClient and its transports.

Re-exports the public surface — Answer, ChoiceQuestion, DecisionClient,
MockTransport, NoulQuestion, Question, ScoreQuestion, top_probability
(plus DEFAULT_THRESHOLD) — so consumers can import the whole seam from one
place. Real OpenRouter transports land with the next issue.

Dual-import contract, mirroring agent/quality.py: this package imports
both as `agent.decision` from the repo root (package-style runtime probes)
and as `decision` from agent/ (tests).
"""

try:  # package-style entry (import agent.decision from the repo root)
    from agent.decision.client import DEFAULT_THRESHOLD, DecisionClient, MockTransport
    from agent.decision.schemas import (
        Answer,
        ChoiceQuestion,
        NoulQuestion,
        Question,
        ScoreQuestion,
        top_probability,
    )
except ModuleNotFoundError:  # script-style entry (import decision from agent/)
    from decision.client import DEFAULT_THRESHOLD, DecisionClient, MockTransport
    from decision.schemas import (
        Answer,
        ChoiceQuestion,
        NoulQuestion,
        Question,
        ScoreQuestion,
        top_probability,
    )

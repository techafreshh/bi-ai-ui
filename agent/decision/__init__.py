"""Typed probabilistic decision layer (issues #8 and #9): the question/answer
schemas, the DecisionClient and its transports.

Re-exports the public surface — Answer, ChoiceQuestion, DecisionClient,
MockTransport, NoulQuestion, Question, ScoreQuestion, top_probability
(plus DEFAULT_THRESHOLD, issue #9's MAX_ATTEMPTS_PER_TRANSPORT); and
from issue #9 TransportError,
DecisionsModalityTransport, ChatStructuredTransport, FallbackChainTransport
and build_decision_client — so consumers can import the whole seam from one
place.

Dual-import contract, mirroring agent/quality.py: this package imports
both as `agent.decision` from the repo root (package-style runtime probes)
and as `decision` from agent/ (tests).
"""

try:  # package-style entry (import agent.decision from the repo root)
    from agent.decision.client import (
        DEFAULT_THRESHOLD,
        MAX_ATTEMPTS_PER_TRANSPORT,
        ChatStructuredTransport,
        DecisionClient,
        DecisionsModalityTransport,
        FallbackChainTransport,
        MockTransport,
        TransportError,
        build_decision_client,
    )
    from agent.decision.schemas import (
        Answer,
        ChoiceQuestion,
        NoulQuestion,
        Question,
        ScoreQuestion,
        top_probability,
    )
except ModuleNotFoundError:  # script-style entry (import decision from agent/)
    from decision.client import (
        DEFAULT_THRESHOLD,
        MAX_ATTEMPTS_PER_TRANSPORT,
        ChatStructuredTransport,
        DecisionClient,
        DecisionsModalityTransport,
        FallbackChainTransport,
        MockTransport,
        TransportError,
        build_decision_client,
    )
    from decision.schemas import (
        Answer,
        ChoiceQuestion,
        NoulQuestion,
        Question,
        ScoreQuestion,
        top_probability,
    )

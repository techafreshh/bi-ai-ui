"""Typed probabilistic decision layer (issues #8 and #9): the question/answer
schemas, the DecisionClient and its transports.

Re-exports the public surface — Answer, ChoiceQuestion, DecisionClient,
MockTransport, NoulQuestion, Question, ScoreQuestion, top_probability
(plus DEFAULT_THRESHOLD, issue #9's MAX_ATTEMPTS_PER_TRANSPORT); and
from issue #9 TransportError,
DecisionsModalityTransport, ChatStructuredTransport, FallbackChainTransport
and build_decision_client, plus issue #19's TypeRoutedTransport and
per_type_model — so consumers can import the whole seam from one place.

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
        TypeRoutedTransport,
        build_decision_client,
        per_type_model,
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
        TypeRoutedTransport,
        build_decision_client,
        per_type_model,
    )
    from decision.schemas import (
        Answer,
        ChoiceQuestion,
        NoulQuestion,
        Question,
        ScoreQuestion,
        top_probability,
    )

# Re-export surface for `from decision import X` consumers (ruff F401 reads
# __all__ as the use-site, so the dual-import blocks above stay flagged as
# intentional re-exports rather than dead imports).
__all__ = [
    "DEFAULT_THRESHOLD",
    "MAX_ATTEMPTS_PER_TRANSPORT",
    "Answer",
    "ChatStructuredTransport",
    "ChoiceQuestion",
    "DecisionClient",
    "DecisionsModalityTransport",
    "FallbackChainTransport",
    "MockTransport",
    "NoulQuestion",
    "Question",
    "ScoreQuestion",
    "TransportError",
    "TypeRoutedTransport",
    "build_decision_client",
    "per_type_model",
    "top_probability",
]

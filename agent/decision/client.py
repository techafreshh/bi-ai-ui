"""DecisionClient: typed probabilistic decisions over a pluggable transport (issue #8).

The GDE pattern's core seam (PRD §2/§7-9): decide a batch of
Choice/Noul/Score questions and return one Answer per valid question, each
carrying probabilities (dict for choice, single float for noul/score) and
an above_threshold flag from the configured DECISION_THRESHOLD.

Transport seam: one question per call — `async def ask(question) -> dict`
returning the RAW answer payload {"id", "type", "probabilities"}. The
client gathers ask() over the batch (asyncio.gather preserves input
order), parses each payload against its question, and drops anything that
fails validation with a WARNING — so a mocked transport can serve delays
in parallel (issue acceptance clause 2: 5 answers x 500ms in <600ms wall
clock) and transport lies get caught per-answer. The PRD's batch wire
format (POST {"questions": [...]}) is the real transports' concern (next
issue); this module only defines the seam they plug into.

Parsing rule table (_parse_answer; every drop logs one WARNING at the
"bi-ai-ui.agent.decision" logger naming the question id, the offending
answer id (or <missing>) and the reason):

  1. payload not a dict / missing the id, type or probabilities key -> drop.
  2. payload id != question id -> drop (the transport answered something
     else; issue acceptance clause 3).
  3. payload type != question type -> drop (clause 1 pins ids AND types).
  4. Scalar probabilities (noul/score): non-bool number in [0, 1] —
     anything else drops the whole answer. NEVER clamped: a clamped 1.5
     would fake confidence.
  5. Choice probabilities: a dict; keys outside question.options are
     dropped with a warning, missing options filled 0.0 (argmax consumers
     need a total dict, PRD §9); any value non-numeric or out of [0, 1]
     drops the whole answer.
  6. Surviving payloads become Answer(id, type, probabilities,
     above_threshold = top_probability(probabilities) >= threshold) in
     question order.

Threshold policy: exactly 0.6 passes (>=) — the PRD's "p<0.6 drops a
component" makes the line inclusive; DEFAULT_THRESHOLD pins that default.
DecisionClient(transport, threshold=None) reads DECISION_THRESHOLD at
construction when threshold is None (env read at use time, mirroring
build_chat_model) and fails loud on anything not a number in [0, 1]. For
noul the scalar is p(yes); for choice the max option probability; for
score the score itself.

Transport exceptions propagate out of decide() — no per-question
resilience: a mock must fail ugly in tests, and per-question fallback
chips belong with the real transports/fallback chain (next issue).

Imports stdlib only — no db/graph/server/model dependency.
"""

import asyncio
import logging
import os

try:  # package-style entry (import agent.decision.client from the repo root)
    from agent.decision import schemas
except ModuleNotFoundError:  # script-style entry (import decision.client from agent/)
    from decision import schemas

logger = logging.getLogger("bi-ai-ui.agent.decision")

# PRD: "p<0.6 drops a component" — the endorsement line defaults to 0.6 and
# is inclusive (exactly 0.6 passes, >=). DECISION_THRESHOLD overrides it.
DEFAULT_THRESHOLD = 0.6


class MockTransport:
    """Loud test double: canned per-question answers, optionally delayed.

    answers maps question id -> the RAW payload dict returned VERBATIM —
    echoing untouched is the point: a test configures a payload whose id or
    type lies about the question it answers, and that is exactly how the
    parsing rule table (issue clauses 2-3) gets exercised. An unconfigured
    question id raises KeyError — a mock that silently invented an answer
    would let a typo'd test config pass as a decision.
    """

    def __init__(self, timeout_ms=0, answers=None):
        self.timeout_ms = timeout_ms
        self.answers = {} if answers is None else answers

    async def ask(self, question):
        """Sleep timeout_ms when set, then return the canned payload for
        question.id verbatim (KeyError when unconfigured)."""
        if self.timeout_ms > 0:
            await asyncio.sleep(self.timeout_ms / 1000)
        return self.answers[question.id]


def _warn_drop(question_id, answer_id, reason):
    """One WARNING per dropped payload, always naming the question id, the
    offending answer id (or <missing>) and the reason — a silently dropped
    decision would be unobservable, which the PRD forbids."""
    logger.warning(
        "dropping answer %s for question %s: %s", answer_id, question_id, reason
    )


def _answer_id_of(raw):
    """Display form of a payload's claimed id for drop messages — a valid
    non-empty string passes through, everything else reads <missing>."""
    raw_id = raw.get("id") if isinstance(raw, dict) else None
    return raw_id if isinstance(raw_id, str) and raw_id else "<missing>"


def _parse_answer(raw, question, threshold) -> schemas.Answer | None:
    """Apply the parsing rule table (module docstring) to one raw transport
    payload against its question — a validated Answer, or None after a
    WARNING when the payload fails any rule."""
    answer_id = _answer_id_of(raw)
    if not isinstance(raw, dict):
        _warn_drop(question.id, answer_id, f"payload is not a dict: {raw!r}")
        return None
    for key in ("id", "type", "probabilities"):
        if key not in raw:
            _warn_drop(question.id, answer_id, f"payload is missing the {key!r} key")
            return None
    if raw["id"] != question.id:
        _warn_drop(
            question.id, answer_id, "payload id does not match the question id"
        )
        return None
    if raw["type"] != question.type:
        _warn_drop(
            question.id,
            answer_id,
            f"payload type {raw['type']!r} does not match question type "
            f"{question.type!r}",
        )
        return None

    value = raw["probabilities"]
    if question.type == "choice":
        if not isinstance(value, dict):
            _warn_drop(
                question.id,
                answer_id,
                f"choice probabilities must be a dict keyed by option, got {value!r}",
            )
            return None
        probabilities = {}
        for option, probability in value.items():
            if option not in question.options:
                # Out-of-catalog option: pruned rather than fatal, so one
                # stray key cannot void an otherwise valid answer — but the
                # pruning itself stays loud.
                logger.warning(
                    "ignoring option %s of answer %s (question %s): not one of "
                    "the question options %s",
                    option,
                    answer_id,
                    question.id,
                    question.options,
                )
                continue
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not 0.0 <= probability <= 1.0
            ):
                _warn_drop(
                    question.id,
                    answer_id,
                    f"probability for option {option!r} must be a number in "
                    f"[0, 1], got {probability!r}",
                )
                return None
            probabilities[option] = float(probability)
        # Missing options read 0.0: argmax consumers need a total dict.
        for option in question.options:
            probabilities.setdefault(option, 0.0)
    else:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= value <= 1.0
        ):
            _warn_drop(
                question.id,
                answer_id,
                f"expected a number in [0, 1] for a {question.type} answer, "
                f"got {value!r} (never clamped)",
            )
            return None
        probabilities = float(value)

    return schemas.Answer(
        id=question.id,
        type=question.type,
        probabilities=probabilities,
        above_threshold=schemas.top_probability(probabilities) >= threshold,
    )


class DecisionClient:
    """Typed probabilistic decision layer over a pluggable transport.

    transport: anything with `async def ask(question) -> dict` answering
    ONE question with its raw payload — MockTransport here, OpenRouter
    batch transports with the next issue. threshold: the above_threshold
    line; None reads DECISION_THRESHOLD (default DEFAULT_THRESHOLD). It is
    resolved and validated once at construction — fail at wiring time, not
    on the first decide() call in a live graph node.
    """

    def __init__(self, transport, threshold=None):
        self.transport = transport
        raw = (
            threshold
            if threshold is not None
            else os.getenv("DECISION_THRESHOLD", DEFAULT_THRESHOLD)
        )
        # bool is an int subclass: float(True) would slip through as 1.0, so
        # reject it before conversion (mirrors queries._require_date).
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError(f"threshold must be a number in [0, 1], got {raw!r}")
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(
                f"threshold must be a number in [0, 1], got {raw!r}"
            ) from None
        if not 0.0 <= value <= 1.0:
            # NaN fails the comparison and lands here too.
            raise ValueError(f"threshold must be a number in [0, 1], got {raw!r}")
        self.threshold = value

    async def decide(self, questions) -> list[schemas.Answer]:
        """Decide a batch of questions -> one Answer per valid (question,
        payload) pair, in question order (issue acceptance clauses 1-4).

        The batch fans out as one transport.ask per question via
        asyncio.gather — parallel INSIDE the client, so 5 x 500ms answers
        return in ~500ms, not 2.5s; gather preserves input order, so
        consumers can zip questions and answers by index. Invalid payloads
        are parsed out (rule table) instead of raised; transport
        exceptions propagate (fail-loud MVP)."""
        if not questions:
            return []
        raws = await asyncio.gather(*(self.transport.ask(q) for q in questions))
        answers = []
        for question, raw in zip(questions, raws):
            answer = _parse_answer(raw, question, self.threshold)
            if answer is not None:
                answers.append(answer)
        return answers

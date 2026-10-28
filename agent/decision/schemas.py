"""Typed question and answer schemas for the decision layer (issue #8).

Three question dataclasses map one-to-one onto the decision wire types
(PRD §8): ChoiceQuestion (type "choice") asks which of several options —
e.g. which catalog components a query needs — and its answer carries
probabilities as a dict {option -> p} (PRD §9); NoulQuestion (type
"noul") is yes/no and its answer carries the single float p(yes);
ScoreQuestion (type "score") asks for a 0-1 rating and its answer carries
the single float score. The issue contract pins exactly these shapes.

Every answer is an Answer(id, type, probabilities, above_threshold):
probabilities mirror the question type's shape, and above_threshold flags
whether the answer's top probability meets the configured
DECISION_THRESHOLD (>= the line) — computed by DecisionClient, never
guessed here.

Plain stdlib dataclasses, NOT pydantic — this module is a leaf the graph
node imports inside an async hot path and must never pull the model
dependency stack; the asked-for validation is the ~15 lines of
__post_init__ below (non-empty id/prompt strings; for choice questions a
total, duplicate-free options tuple, since answer dicts and argmax
consumers are keyed by it). Everything fails loud with ValueError,
mirroring the queries/quality/catalog idiom.

top_probability(probabilities) is the single definition of "how confident
is this answer" — the scalar itself for noul/score, the max option
probability (default 0.0) for choice — so threshold flagging stays
consistent everywhere.

Additive by contract: imports only stdlib — no db/graph/server/pydantic
dependency — so the client, the decide node and later transports can
import it from any context.
"""

from dataclasses import dataclass


def _require_text(value, label):
    """`value` must be a non-empty string, else ValueError — the fail-loud
    idiom of queries._require_date applied to identity fields."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string, got {value!r}")
    return value


@dataclass
class ChoiceQuestion:
    """One multiple-choice decision question (wire type "choice").

    options is normalized to a tuple in __post_init__ — the answer's
    probabilities dict is keyed by these exact strings, so the tuple must
    be total (>= 2), duplicate-free and empty-string-free or construction
    fails: a duplicate or blank option would make argmax consumers read an
    ambiguous or unanswerable key.

    multi=True marks a co-occurring selection (the catalog question): the
    options are independently testable, not mutually exclusive, so the
    alpha transport fans them out into one noul per option and folds the
    answers back into a single choice-shaped probability map. Chat-style
    transports still see one choice question with a per-option probability
    instruction.
    """

    id: str
    prompt: str
    options: list[str] | tuple[str, ...]
    type: str = "choice"
    multi: bool = False

    def __post_init__(self):
        _require_text(self.id, "id")
        _require_text(self.prompt, "prompt")
        if not isinstance(self.multi, bool):
            raise ValueError(f"{self.id}: multi must be a bool, got {self.multi!r}")
        if isinstance(self.options, str) or not isinstance(self.options, (list, tuple)):
            raise ValueError(
                f"{self.id}: options must be a list or tuple of strings, "
                f"got {self.options!r}"
            )
        options = tuple(self.options)
        if len(options) < 2:
            raise ValueError(
                f"{self.id}: options needs at least 2 entries to pick between, "
                f"got {options!r}"
            )
        for option in options:
            if not isinstance(option, str) or not option:
                raise ValueError(
                    f"{self.id}: each option must be a non-empty string, got {option!r}"
                )
        if len(set(options)) != len(options):
            raise ValueError(f"{self.id}: options contains duplicates: {options!r}")
        self.options = options


@dataclass
class NoulQuestion:
    """One yes/no decision question (wire type "noul"); its answer's scalar
    probability is p(yes) — e.g. the PRD's Noul("data problem?")≈0.94 gates
    the data-quality banner."""

    id: str
    prompt: str
    type: str = "noul"

    def __post_init__(self):
        _require_text(self.id, "id")
        _require_text(self.prompt, "prompt")


@dataclass
class ScoreQuestion:
    """One 0-1 rating decision question (wire type "score"); its answer's
    scalar probability IS the score."""

    id: str
    prompt: str
    type: str = "score"

    def __post_init__(self):
        _require_text(self.id, "id")
        _require_text(self.prompt, "prompt")


@dataclass
class Answer:
    """One answered question as DecisionClient returns it (issue contract).

    probabilities is a dict {option -> p} for choice questions and a single
    float for noul/score; above_threshold says whether the answer's top
    probability meets the configured threshold (>= — the issue drops a
    component on p < threshold, so exactly at the line passes). The client
    validates payloads before constructing one of these; the __post_init__
    guards misuse (e.g. a dict where a float belongs) the same loud way.
    """

    id: str
    type: str
    probabilities: dict[str, float] | float
    above_threshold: bool

    def __post_init__(self):
        _require_text(self.id, "id")
        _require_text(self.type, "type")
        if isinstance(self.probabilities, bool) or not isinstance(
            self.probabilities, (dict, int, float)
        ):
            raise ValueError(
                "probabilities must be a dict (choice) or a single number in "
                f"[0, 1] (noul/score), got {self.probabilities!r}"
            )
        if not isinstance(self.above_threshold, bool):
            raise ValueError(
                f"above_threshold must be a bool, got {self.above_threshold!r}"
            )


# Union alias for signatures taking any question kind — the `type` field is
# the discriminator; deliberately no base class (PRD §8 fixes exactly these
# three wire types).
Question = ChoiceQuestion | NoulQuestion | ScoreQuestion


def top_probability(probabilities) -> float:
    """The answer's headline confidence in [0, 1] — what above_threshold is
    compared against.

    A dict (choice) reads as its max option probability, defaulting to 0.0
    for an empty dict (no endorsement anywhere); a scalar (noul/score)
    reads as itself. Expects client-validated input (non-bool numbers in
    [0, 1]) — float() here is a coercion, not a re-validation.
    """
    if isinstance(probabilities, dict):
        return max((float(value) for value in probabilities.values()), default=0.0)
    return float(probabilities)

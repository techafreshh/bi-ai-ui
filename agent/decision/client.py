"""DecisionClient: typed probabilistic decisions over a pluggable transport
(issues #8 seam + #9 live transports).

The GDE pattern's core seam (PRD §2/§7-9): decide a batch of
Choice/Noul/Score questions and return one Answer per valid question, each
carrying probabilities (dict for choice, single float for noul/score) and
an above_threshold flag from the configured DECISION_THRESHOLD.

Transport seam: transports answer with their RAW payloads, either per
question — `async def ask(question) -> dict` (#8's contract, served by
MockTransport) — or as one batch call — `async def ask_batch(questions) ->
dict[question_id, raw_payload]` (the real OpenRouter transports, issue #9),
detected via duck-typing (getattr). The PRD §10 batch wire body (POST
{"questions": [...]}) carries one state snapshot, so the batch path gets a
single call with the whole question list; per-question transports keep the
asyncio.gather fan-out (parallel INSIDE the client, so 5 x 500ms answers
return in ~500ms, not 2.5s). Both paths preserve question order and parse
each payload against its question, dropping anything that fails validation
with a WARNING — so transport lies get caught per-answer. The transports
themselves (issue #9): DecisionsModalityTransport POSTs the batch wire
format to the OpenRouter decisions-modality endpoint for DECISION_MODEL;
ChatStructuredTransport walks a model chain over chat completions with
response_format: json_schema; FallbackChainTransport walks the two with
<= MAX_ATTEMPTS_PER_TRANSPORT attempts each and raises TransportError when
every transport is exhausted.

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
resilience: a mock must fail ugly in tests. Transport-level resilience is
exactly what the fallback chain (FallbackChainTransport) provides across
transports, with bounded attempts (no infinite retries).

Imports stdlib + httpx — no db/graph/server/model dependency.
"""

import asyncio
import json
import logging
import os

import httpx

try:  # package-style entry (import agent.decision.client from the repo root)
    from agent.decision import schemas
except ModuleNotFoundError:  # script-style entry (import decision.client from agent/)
    from decision import schemas

logger = logging.getLogger("bi-ai-ui.agent.decision")

# PRD: "p<0.6 drops a component" — the endorsement line defaults to 0.6 and
# is inclusive (exactly 0.6 passes, >=). DECISION_THRESHOLD overrides it.
DEFAULT_THRESHOLD = 0.6

# Issue #9 transport plumbing. Env names mirror graph.py's chat-model wiring:
# read at construction, fail loud. DECISION_MODEL names the OpenRouter
# decisions-modality model (PRD §8: ~perplexity/pplx-decider-flash-latest);
# DECISION_FALLBACK_MODELS is the comma-separated chat-completions chain with
# response_format: json_schema (anthropic/claude-haiku-5.5 is a documented
# alternative member). DECISION_TIMEOUT_S bounds every request so the live
# acceptance budget (answers within 10s) cannot hang on a stuck endpoint.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_DECISION_MODEL = "~perplexity/pplx-decider-flash-latest"
DEFAULT_FALLBACK_MODELS = ("inception/mercury-decide:free", "openai/gpt-6-luna")
DEFAULT_DECISION_TIMEOUT_S = 8.0
# Bounded retries (issue acceptance clause 3): at most this many ask_batch
# attempts per transport in the fallback chain — one attempt is one HTTP
# request — so worst case is 2 decisions + 2 models x 2 chain attempts = 6
# requests, then TransportError. Never an infinite retry loop.
MAX_ATTEMPTS_PER_TRANSPORT = 2
# Mirrors graph.py's message verbatim: one missing-key explanation, one fix.
_NO_KEY_MESSAGE = (
    "OPENROUTER_API_KEY is not set. Copy agent/.env.example to agent/.env, add your "
    "OpenRouter key, and restart the agent."
)


class TransportError(RuntimeError):
    """A decision transport failed after its bounded attempts (issue #9):
    network error, non-200 status, unusable body, or an exhausted fallback
    chain. Propagates out of decide() per the #8 fail-loud rule — no
    per-question resilience, no silent canned defaults in production."""


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


def _answers_mapping(raw):
    """Normalize a decision response body to {answer_id: raw_payload}.

    Accepts the raw wire body {"answers": [...]} or the contract spike's
    {"meta": ..., "response": {...}} wrapper (one level of unwrapping, so
    the committed snapshot parses like the live body it captured — issue
    acceptance clause 1). Entries that are not dicts with a non-empty
    string id are dropped with a WARNING (they cannot be mapped to a
    question); a body without a usable answers list — or with no usable
    entries at all — raises TransportError so the fallback chain engages.
    """
    if (
        isinstance(raw, dict)
        and "answers" not in raw
        and isinstance(raw.get("response"), dict)
    ):
        raw = raw["response"]
    if not isinstance(raw, dict):
        raise TransportError(f"decision response is not a JSON object: {raw!r}")
    answers = raw.get("answers")
    if not isinstance(answers, list) or not answers:
        raise TransportError(
            f"decision response has no non-empty 'answers' list: {raw!r}"
        )
    mapping = {}
    for entry in answers:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
            mapping[entry["id"]] = entry
        else:
            # Same one-WARNING-per-drop idiom as the rule table; there is no
            # question to name here, so the batch itself takes that slot.
            _warn_drop(
                "<batch>",
                _answer_id_of(entry),
                "answer entry has no usable string id; it cannot be mapped to "
                "a question",
            )
    if not mapping:
        raise TransportError("decision response contained no usable answers")
    return mapping


def _question_payload(question):
    """PRD §10 wire shape for one question — options only on choice
    questions (noul/score carry none)."""
    payload = {"type": question.type, "id": question.id, "prompt": question.prompt}
    if question.type == "choice":
        payload["options"] = list(question.options)
    return payload


def _resolve_timeout(timeout_s):
    """Shared DECISION_TIMEOUT_S plumbing: explicit argument wins, then the
    env var, then the default — validated once at construction like the
    DecisionClient threshold (fail at wiring time, not mid-decide). Never a
    bool, always a positive finite number of seconds (NaN fails the
    comparison and lands in the same error)."""
    raw = (
        timeout_s
        if timeout_s is not None
        else os.getenv("DECISION_TIMEOUT_S", DEFAULT_DECISION_TIMEOUT_S)
    )
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError(f"timeout must be a positive number of seconds, got {raw!r}")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(
            f"timeout must be a positive number of seconds, got {raw!r}"
        ) from None
    if not value > 0:
        raise ValueError(f"timeout must be a positive number of seconds, got {raw!r}")
    return value


class DecisionsModalityTransport:
    """Primary transport: the OpenRouter decisions-modality endpoint.

    POSTs the PRD §10 batch wire format — {"model", "state", "questions"}
    onto {OPENROUTER_BASE_URL}/decisions for DECISION_MODEL (default
    ~perplexity/pplx-decider-flash-latest), bearer-authenticated, and
    normalizes the {"answers": [...]} body to id -> raw payload. One
    ask_batch call is ONE HTTP request; retries belong to the fallback
    chain (MAX_ATTEMPTS_PER_TRANSPORT bounds this transport to <=2
    requests). Any non-200 or unusable body raises TransportError so the
    chain can engage ChatStructuredTransport.

    parse(snapshot) is the shared answer normalizer as a staticmethod over
    the response body alone — no transport state, no API key — so tests and
    the contract snapshot can be parsed without constructing anything.
    """

    def __init__(
        self,
        api_key=None,
        model=None,
        base_url=None,
        state=None,
        http_client=None,
        timeout_s=None,
    ):
        self.api_key = (
            api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
        )
        if not self.api_key:
            # Acceptance clause 4: a clear configuration error at construction,
            # not a 401 deep inside the first live decide call.
            raise RuntimeError(_NO_KEY_MESSAGE)
        self.model = (
            model
            if model is not None
            else os.getenv("DECISION_MODEL", DEFAULT_DECISION_MODEL)
        )
        self.base_url = (
            base_url
            if base_url is not None
            else os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL)
        ).rstrip("/")
        # Per-request state snapshot (PRD §10); the decide node (issue #10)
        # will own per-request plumbing — for now the constructor holds it.
        self.state = {} if state is None else state
        # http_client is the test seam (httpx.MockTransport): an injected
        # client is never owned, so aclose() leaves it alone.
        self.http_client = http_client
        self.timeout_s = _resolve_timeout(timeout_s)
        self._owned_client = None
        # The contract spike records the raw response body verbatim; kept on
        # the instance (not returned) so ask_batch keeps its payload mapping.
        self.last_raw_response = None

    @staticmethod
    def parse(snapshot):
        """Normalize a decisions response body (or spike snapshot) to
        {answer_id: raw_payload}; see _answers_mapping."""
        return _answers_mapping(snapshot)

    def _client(self):
        if self.http_client is not None:
            return self.http_client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._owned_client

    def _request_body(self, questions):
        return {
            "model": self.model,
            "state": self.state or {},
            "questions": [_question_payload(q) for q in questions],
        }

    async def ask_batch(self, questions):
        """POST the batch wire format once; return {id: raw_payload} or raise
        TransportError (non-200, non-JSON, unusable body, network error)."""
        try:
            response = await self._client().post(
                f"{self.base_url}/decisions",
                json=self._request_body(questions),
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            # httpx's own TransportError (connection/DNS) is wrapped too, so
            # the chain only ever sees one failure type.
            raise TransportError(f"decisions endpoint request failed: {exc}") from exc
        if response.status_code != 200:
            raise TransportError(
                f"decisions endpoint returned HTTP {response.status_code} for "
                f"model {self.model}: {response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TransportError(
                f"decisions endpoint returned a non-JSON body for model "
                f"{self.model}: {exc}"
            ) from exc
        mapping = _answers_mapping(body)
        self.last_raw_response = body
        return mapping

    async def aclose(self):
        """Close the internally-owned HTTP client, if one was built."""
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


class ChatStructuredTransport:
    """Fallback transport: chat completions with JSON-schema structured output.

    Walks the model chain (DECISION_FALLBACK_MODELS, default
    inception/mercury-decide:free then openai/gpt-6-luna) over
    {OPENROUTER_BASE_URL}/chat/completions with response_format: json_schema,
    asking the model to reply with the same {"answers": [...]} shape. One
    model per failed attempt: a FallbackChainTransport retry re-walks the
    chain from the first model. `strict` is deliberately omitted — smaller
    free/flash models that do not implement strict mode would 400; the
    client's parsing rule table remains the real validator.
    """

    def __init__(
        self,
        models=None,
        api_key=None,
        base_url=None,
        state=None,
        http_client=None,
        timeout_s=None,
    ):
        self.api_key = (
            api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
        )
        if not self.api_key:
            raise RuntimeError(_NO_KEY_MESSAGE)
        if models is None:
            raw = os.getenv("DECISION_FALLBACK_MODELS", "")
            models = (
                [m.strip() for m in raw.split(",") if m.strip()]
                if raw.strip()
                else list(DEFAULT_FALLBACK_MODELS)
            )
        elif isinstance(models, str):
            models = [m.strip() for m in models.split(",") if m.strip()]
        if not models:
            raise ValueError(
                "no fallback models configured: DECISION_FALLBACK_MODELS must "
                "name at least one chat model"
            )
        self.models = list(models)
        self.base_url = (
            base_url
            if base_url is not None
            else os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL)
        ).rstrip("/")
        self.state = {} if state is None else state
        self.http_client = http_client
        self.timeout_s = _resolve_timeout(timeout_s)
        self._owned_client = None
        self.last_raw_response = None

    def _client(self):
        if self.http_client is not None:
            return self.http_client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._owned_client

    def _request_body(self, model, questions):
        return {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Answer with JSON only: an object with a single \"answers\" "
                        "array. Each answer entry carries \"id\" and \"type\" copied "
                        "from its question, and \"probabilities\" — a dict of option "
                        "to probability for choice questions, a single number in "
                        "[0, 1] for noul/score questions."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "state": self.state or {},
                            "questions": [_question_payload(q) for q in questions],
                        }
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision_answers",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "answers": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "type": {
                                            "type": "string",
                                            "enum": ["choice", "noul", "score"],
                                        },
                                        # dict for choice / number for noul+score: a
                                        # oneOf would 400 non-strict models, so the
                                        # schema stays permissive here — the client's
                                        # parsing rule table is the real validator.
                                        "probabilities": {
                                            "description": (
                                                "option->probability dict for choice, "
                                                "single number for noul/score"
                                            )
                                        },
                                    },
                                    "required": ["id", "type", "probabilities"],
                                },
                            }
                        },
                        "required": ["answers"],
                    },
                },
            },
        }

    async def _ask_one(self, model, questions):
        """One chat-completions request for one model; every failure mode is
        wrapped into a TransportError naming the model."""
        try:
            response = await self._client().post(
                f"{self.base_url}/chat/completions",
                json=self._request_body(model, questions),
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"chat model {model}: request failed: {exc}") from exc
        if response.status_code != 200:
            raise TransportError(
                f"chat model {model}: HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise TransportError(
                f"chat model {model}: unusable response shape: {exc}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise TransportError(f"chat model {model}: empty message content")
        try:
            answers = json.loads(content)
        except ValueError as exc:
            raise TransportError(
                f"chat model {model}: message content is not JSON: {exc}"
            ) from exc
        try:
            mapping = _answers_mapping(answers)
        except TransportError as exc:
            raise TransportError(f"chat model {model}: {exc}") from exc
        self.last_raw_response = payload
        return mapping

    async def ask_batch(self, questions):
        """Walk the model chain once, one model per attempt; return the first
        model's answer mapping or raise TransportError naming each failure."""
        failures = []
        for index, model in enumerate(self.models):
            try:
                return await self._ask_one(model, questions)
            except TransportError as exc:
                logger.warning(
                    "chat fallback model %s (%d of %d) failed: %s",
                    model,
                    index + 1,
                    len(self.models),
                    exc,
                )
                failures.append(f"{model}: {exc}")
        raise TransportError(
            "all chat fallback models failed — " + "; ".join(failures)
        )

    async def aclose(self):
        """Close the internally-owned HTTP client, if one was built."""
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


class FallbackChainTransport:
    """Bounded fallback chain over transports exposing ask_batch (issue #9).

    Walks transports in order; each gets up to MAX_ATTEMPTS_PER_TRANSPORT
    ask_batch attempts (one attempt = one HTTP request for the transports in
    this module). Exceptions, TransportError and empty mappings count as
    failures and are logged with one WARNING per attempt naming the
    transport class; the first non-empty answer mapping is returned. When
    every transport is exhausted, TransportError summarizes each transport's
    last failure — bounded, never an infinite retry loop (acceptance
    clause 3).
    """

    def __init__(self, transports):
        self.transports = list(transports)
        if not self.transports:
            raise ValueError("FallbackChainTransport needs at least one transport")

    async def ask_batch(self, questions):
        failures = []
        for transport in self.transports:
            last_error = None
            for attempt in range(1, MAX_ATTEMPTS_PER_TRANSPORT + 1):
                try:
                    mapping = await transport.ask_batch(questions)
                except Exception as exc:  # any transport failure is a retryable one
                    last_error = exc
                    logger.warning(
                        "%s attempt %d/%d failed: %s",
                        type(transport).__name__,
                        attempt,
                        MAX_ATTEMPTS_PER_TRANSPORT,
                        exc,
                    )
                else:
                    if mapping:
                        return mapping
                    last_error = TransportError("transport returned no answers")
                    logger.warning(
                        "%s attempt %d/%d returned no answers",
                        type(transport).__name__,
                        attempt,
                        MAX_ATTEMPTS_PER_TRANSPORT,
                    )
            failures.append(f"{type(transport).__name__}: {last_error}")
        raise TransportError(
            f"all decision transports failed after {MAX_ATTEMPTS_PER_TRANSPORT} "
            "attempts each — " + "; ".join(failures)
        )

    async def aclose(self):
        """Close internally-owned HTTP clients on every member transport."""
        for transport in self.transports:
            closer = getattr(transport, "aclose", None)
            if closer is not None:
                await closer()


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

    transport: a per-question transport — `async def ask(question) -> dict`
    answering ONE question with its raw payload (MockTransport) — or a batch
    transport — `async def ask_batch(questions) -> dict[question_id,
    raw_payload]` (DecisionsModalityTransport / ChatStructuredTransport /
    FallbackChainTransport, issue #9); detected via duck-typing. threshold:
    the above_threshold line; None reads DECISION_THRESHOLD (default
    DEFAULT_THRESHOLD). It is
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

        Transports are detected by capability: a batch transport exposing
        ask_batch(questions) -> dict gets ONE call with the whole question
        list — the PRD §10 wire body carries one state snapshot, so
        per-question POSTs would re-send `state` N times and fight the
        10s live budget (issue #9); per-question transports (#8's ask()
        contract) keep the asyncio.gather fan-out — parallel INSIDE the
        client, so 5 x 500ms answers return in ~500ms, not 2.5s. Both
        paths preserve input order, so consumers can zip questions and
        answers by index. Questions the batch mapping does not answer warn
        and drop (one WARNING each, <missing> as the answer id); invalid
        payloads are parsed out (rule table) instead of raised; transport
        exceptions propagate (fail-loud MVP)."""
        if not questions:
            return []
        answers = []
        ask_batch = getattr(self.transport, "ask_batch", None)
        if ask_batch is not None:
            mapping = await ask_batch(questions)
            for question in questions:
                raw = mapping.get(question.id)
                if raw is None:
                    # One WARNING per missing question id, then parse as
                    # dropped — deliberately not fed through the rule table
                    # as None, which would warn a second time per question.
                    _warn_drop(
                        question.id,
                        "<missing>",
                        "no answer returned for this question id",
                    )
                    continue
                answer = _parse_answer(raw, question, self.threshold)
                if answer is not None:
                    answers.append(answer)
        else:
            raws = await asyncio.gather(*(self.transport.ask(q) for q in questions))
            for question, raw in zip(questions, raws):
                answer = _parse_answer(raw, question, self.threshold)
                if answer is not None:
                    answers.append(answer)
        return answers


def build_decision_client(threshold=None):
    """Wire the production decision client (issue #9): the OpenRouter
    decisions-modality transport first, the chat structured-output fallback
    chain second, both under the bounded FallbackChainTransport.

    Reads OPENROUTER_API_KEY / DECISION_MODEL / DECISION_FALLBACK_MODELS /
    DECISION_TIMEOUT_S / OPENROUTER_BASE_URL at construction — a missing key
    raises RuntimeError HERE, at wiring time (acceptance clause 4), not deep
    inside the first live decide call.
    """
    return DecisionClient(
        FallbackChainTransport(
            [DecisionsModalityTransport(), ChatStructuredTransport()]
        ),
        threshold=threshold,
    )

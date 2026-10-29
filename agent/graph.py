"""The bi-ai-ui LangGraph agent: a five-node GDE graph streaming shared
state to CopilotKit (issue #10).

    START → read_state → decide → layout → query → fill → END

  * read_state — the active sandbox world's aggregates + data-quality
    stats into `snapshot` (window-scoped KPIs via queries.py, anomaly-
    scoped checks plus `snapshot["dq_scope"]`, the churn delta), stamped
    with `snapshot["dataset_id"]` verbatim. The window anchors on the
    active source's `latest_event_day` — never the wall clock or the legacy
    default DB. With no active dataset the snapshot stays honest-empty
    (`dataset_id: None`, `empty: True`) without opening any DB; a missing
    or unreadable active DB degrades into an empty-handed snapshot instead
    of raising — the graph must complete wherever it runs.
  * decide — questions.build_question_set's fixed catalog question set,
    fanned out through DecisionClient.decide (the #8 seam) over the wired
    transport (#9's OpenRouter chain when a key is configured, the
    deterministic HeuristicTransport otherwise, issue #18's guard when the
    query names no data domain; DECISION_MODEL_CHOICE/NOUL/SCORE, issue
    #19, route a question type to its own decision model). Transport
    failures or unparsable answers fall back to a fresh heuristic client,
    so `decisions` always carries 1..7 entries stamped with {id, type,
    probabilities, pick, model, latency_ms} — model attributed per answer
    from the transport's declaration, then the type's override, then
    DECISION_MODEL, never a guessed value.
  * layout — assembles a complete, valid draft of above-threshold catalog
    components from the snapshot and cuts it with
    catalog.filter_layout(items, probs, threshold); nothing surviving falls
    back to the insight_text-only default view. The emitted `a2ui` is
    parse-asserted through A2UILayout inside this node (issue acceptance
    clause 4) — an invalid draft never reaches the frontend. Investigate
    follow-ups (issue #16, messages starting with the fixed
    INVESTIGATE_PREFIX) bypass the cut: the CTA-authored drill-down layout
    (filtered cohort data_table + titled insight_text) replaces the canvas.
  * query — slot-scoped SQL for the surviving slots (window-scoped DQ
    checks, churn cohort rows, churn delta), merged back into `a2ui` and
    re-validated before storing. One churn_driver run (issue #16) backs the
    callout's cause story, the cohort table rows, and — on investigate runs
    — the drill-down table's row-level detail and insight body.
  * fill — exactly ONE text-model call writing the narrative into
    `a2ui.slotCopy` ("kpis.title", "line_chart.title", "insight_text.body",
    "summary"), streamed via model.astream so token deltas still reach the
    chat. Any model error (including a missing OPENROUTER_API_KEY, which
    build_chat_model raises lazily) degrades to deterministic copy from the
    KPIs, keeping the run green.

Shared state fields `a2ui`, `decisions[]` and `status` (plus the
`snapshot` working set and `messages`) are plain JSON-safe dicts in the
graph's output schema, so ag-ui-langgraph's schema-filtered
STATE_SNAPSHOT emits them to the client on every node exit — no manual
event assembly.
"""

import json
import logging
import os
import re
import time
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from pydantic import ValidationError
from typing_extensions import Annotated, NotRequired, TypedDict

try:  # package-style entry (import agent.graph from the repo root)
    from agent import catalog, quality, queries, questions
    from agent.data import PostgresSource, SqliteSource, use_postgres
    from agent.data.protocol import Source
    from agent.decision import client as decision_client
    from agent.decision.heuristic import HeuristicTransport, classify
except ModuleNotFoundError:  # script-style entry (tests, server.py from agent/)
    import catalog
    import quality
    import queries
    import questions
    from data import PostgresSource, SqliteSource, use_postgres
    from data.protocol import Source
    from decision import client as decision_client
    from decision.heuristic import HeuristicTransport, classify

logger = logging.getLogger("bi-ai-ui.agent.graph")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TEXT_MODEL = "openai/gpt-6-luna"
_NO_KEY_MESSAGE = (
    "OPENROUTER_API_KEY is not set. Copy agent/.env.example to agent/.env, add your "
    "OpenRouter key, and restart the agent."
)

# One system prompt for the skeleton; persona copy comes with later phases.
SYSTEM_PROMPT = (
    "You are the bi-ai-ui agent, a concise assistant for a business "
    "intelligence dashboard. Answer briefly in plain text."
)

# Status literals streamed through `status` as the run advances (issue #10).
STATUSES = {
    "read_state": "reading_state",
    "decide": "deciding",
    "layout": "building_layout",
    "query": "querying",
    "fill": "done",
}

# Issue #16 golden path B: the Investigate CTA the churn callout carries and
# the fixed prefix of the follow-up message that CTA triggers. The cta slot
# is the machine id (catalog.VarianceCalloutProps.cta; the frontend
# humanizes it for the button label), the prefix is a contract the frontend's
# useInvestigate hook mirrors verbatim in web/src/hooks/useInvestigate.tsx.
INVESTIGATE_CTA = "investigate_churn_cohort"
INVESTIGATE_PREFIX = "[investigate] churn cohort"

# The drill-down data_table's columns — exactly the row-level keys
# churn_cohort_detail returns (VirtualTable cells look up row[column]).
_INVESTIGATE_COLUMNS = [
    "segment",
    "plan",
    "mrr",
    "tenure_months",
    "cancelled_on",
    "repriced",
]


def _is_investigate(text) -> bool:
    """True when the user's message is the Investigate follow-up: the fixed
    INVESTIGATE_PREFIX (CTA framing), any leading whitespace tolerated and
    the match case-insensitive. Non-string input reads False."""
    return isinstance(text, str) and text.lstrip().lower().startswith(
        INVESTIGATE_PREFIX
    )


class AgentState(TypedDict):
    """Shared graph state — every field is a plain JSON-safe value so the
    ag-ui-langgraph adapter's schema-filtered STATE_SNAPSHOT can serialize
    it. Pydantic models are deliberately never stored (their dicts are)."""

    messages: Annotated[list, add_messages]
    a2ui: NotRequired[dict | None]
    decisions: NotRequired[list[dict]]
    status: NotRequired[str]
    snapshot: NotRequired[dict]


def build_chat_model() -> ChatOpenAI:
    """Build the OpenRouter-backed chat model lazily.

    Lazy so a missing key can never break module import or /health: the
    failure surfaces only when a fill request actually needs the model.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(_NO_KEY_MESSAGE)
    return ChatOpenAI(
        model=os.getenv("TEXT_MODEL", DEFAULT_TEXT_MODEL),
        api_key=api_key,
        # Default is OpenRouter; overridable so tests can point at a fake
        # OpenAI-compatible server and verify the whole streaming path.
        base_url=os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
        streaming=True,
    )


# --- Decision wiring ----------------------------------------------------------


def _last_user_text(messages) -> str:
    """The most recent human message's text — the query the graph answers.
    Non-string content (block lists) degrades to empty; decide recomputes
    whatever it can from the snapshot alone."""
    for message in reversed(messages or []):
        if getattr(message, "type", None) == "human":
            content = message.content
            return content if isinstance(content, str) else ""
    return ""


class _TimedTransport:
    """Wall-clock shim over a decision transport.

    Wraps whatever transport get_decision_client wired and records how long
    each ask took (batch transports get one shared wall-clock entry that is
    expanded to every question in the batch). `ask_batch` is exposed only
    when the wrapped transport has one, preserving DecisionClient's
    duck-typing detection of per-question vs batch transports.
    """

    def __init__(self, transport):
        self.transport = transport
        # One {question_id: elapsed_ms} map per ask call, in call order.
        self.timings = []
        if hasattr(transport, "ask_batch"):
            self.ask_batch = self._timed_ask_batch

    @property
    def model(self):
        """Forward the wrapped transport's declared model (or lack of one)."""
        return getattr(self.transport, "model", None)

    async def ask(self, question):
        started = time.monotonic()
        try:
            return await self.transport.ask(question)
        finally:
            self.timings.append({question.id: (time.monotonic() - started) * 1000.0})

    async def _timed_ask_batch(self, questions_):
        started = time.monotonic()
        try:
            return await self.transport.ask_batch(questions_)
        finally:
            # One HTTP call answers the whole batch: the shared wall clock
            # is the per-question latency the wire actually took.
            elapsed_ms = (time.monotonic() - started) * 1000.0
            self.timings.append({q.id: elapsed_ms for q in questions_})

    def timing_by_id(self):
        """question_id -> milliseconds, later calls overwriting earlier ones
        (a fallback re-run's timings win — that is the run that decided)."""
        merged = {}
        for chunk in self.timings:
            merged.update(chunk)
        return merged


def _decision_model_label(transport, answer_type) -> str:
    """Which model answered one answer: a transport-declared label first (the
    heuristic declares "heuristic", the #9 transports declare their model),
    then the answer type's DECISION_MODEL_<TYPE> override (issue #19), then
    the DECISION_MODEL env (the fallback chain's own default) — so decisions
    never store a guessed value as fact."""
    declared = getattr(transport, "model", None)
    if isinstance(declared, str) and declared:
        return declared
    override = decision_client.per_type_model(answer_type)
    if override:
        return override
    return os.getenv("DECISION_MODEL", decision_client.DEFAULT_DECISION_MODEL)


_decision_client = None


def _wire_decision_client() -> decision_client.DecisionClient:
    """The process-wide decision client: #9's OpenRouter chain when a key
    is configured and the factory imports, the deterministic heuristic
    transport otherwise. Wiring failures (a bad DECISION_THRESHOLD, a
    broken env) also degrade to the heuristic rather than hard-failing the
    node — resilience here is issue #10's job; fail-loud wiring was #9's."""
    build = getattr(decision_client, "build_decision_client", None)
    if build is not None and os.getenv("OPENROUTER_API_KEY"):
        try:
            return build()
        except Exception as exc:  # noqa: BLE001 - any wiring failure degrades
            logger.warning(
                "decision client wiring failed (%s); using the heuristic transport",
                exc,
            )
    return decision_client.DecisionClient(HeuristicTransport())


def get_decision_client() -> decision_client.DecisionClient:
    """Memoized decision singleton — one client, therefore one set of #9
    httpx connection pools, for the whole process (per-request construction
    would leak them). Tests inject doubles by monkeypatching this."""
    global _decision_client
    if _decision_client is None:
        _decision_client = _wire_decision_client()
    return _decision_client


def reset_decision_client() -> None:
    """Test seam: forget the memoized client so the next get_decision_client
    re-wires. Production never resets; the dropped client's pools are
    reclaimed by the interpreter at exit."""
    global _decision_client
    _decision_client = None


# --- Node: read_state ---------------------------------------------------------


async def read_state(state: AgentState) -> dict:
    """Aggregate the active dataset's facts the rest of the run works from.

    The single-active sandbox world (`datasets.get_active()`) scopes every
    read: the window anchors on the active source's own latest event day —
    the live Postgres source when `"postgres"` is active, else the active
    sandbox file — never the wall clock or the legacy default DB. With no
    active dataset the snapshot stays honest-empty (`dataset_id: None`,
    `empty: True`) without opening any DB, so rows can never leak across
    datasets; the downstream nodes short-circuit on the flag. Aggregates
    (window-scoped KPIs, decision-context DQ checks, churn delta) ride the
    queries/quality seam, which resolves the same active path. An
    unreadable active DB degrades: the snapshot stays JSON-shaped with
    `degraded: True` and the window falls back to the wall-clock anchor's
    latest complete quarter."""
    try:  # function-local so importing graph never touches the registry
        # (mirrors db._registry_active_path's cycle-avoiding idiom).
        try:  # package-style entry (import agent.graph from the repo root)
            from agent import datasets
        except ModuleNotFoundError:  # script-style entry (tests, server.py)
            import datasets  # type: ignore[no-redef]
        active_id = datasets.get_active()
    except Exception:  # noqa: BLE001 - registry trouble reads as no dataset
        active_id = None
    user_text = _last_user_text(state.get("messages") or [])
    snapshot: dict = {
        "query": user_text,
        # The sandbox world this run reads: the active id verbatim
        # ("postgres" included), None when no dataset is active.
        "dataset_id": active_id,
        # Honest-empty: no active dataset means no world to query.
        "empty": active_id is None,
        "degraded": active_id is None,
        "data_end": None,
        "investigate": _is_investigate(user_text),
        # Issue #17: the detected anomaly window — None until (and unless)
        # the aggregates below prove the data is dirty somewhere recent, so
        # degraded runs carry the key with its 'no anomaly' value.
        "dq_scope": None,
        "window": None,
    }
    if active_id is None:
        # No world to query: return without opening any DB — the downstream
        # nodes short-circuit on snapshot["empty"] and the run still
        # completes with the honest-empty shape.
        return {"snapshot": snapshot, "status": STATUSES["read_state"]}

    data_end = None
    window = None
    try:
        # The data_end anchor resolves through the Source protocol — never
        # a direct DB read — scoped to the single active world; an
        # unreadable source degrades (data_end stays None) instead of
        # raising.
        if active_id == "postgres" and use_postgres():
            source: Source = PostgresSource()
        else:
            source = SqliteSource(datasets.active_path())
        data_end = source.latest_event_day()
        window = questions.resolve_window(user_text, data_end=data_end)
    except Exception as exc:  # noqa: BLE001 - DB problems degrade, never raise
        logger.error("read_state: data read failed (%s); degrading snapshot", exc)
        snapshot["degraded"] = True
    if window is None:
        # No readable DB: the anchor degrades to the wall clock so the run
        # still carries a well-formed window.
        window = questions.resolve_window(user_text, data_end=None)
    start, end = window
    snapshot["data_end"] = data_end
    snapshot["window"] = {"start": start, "end": end, "range": f"{start}..{end}"}

    try:
        snapshot["kpis"] = queries.kpis(start, end)
        snapshot["previous_kpis"] = queries.kpis(
            *questions.previous_quarter((start,))
        )
        # Issue #17: the decision-context DQ checks read the window where
        # the data is actually dirty (the detected anomaly scope) when one
        # exists, so "Is the data clean?" decides on the corrupted window
        # instead of a diluting trailing-30 default; the display window can
        # only match it by coincidence, which is why `query` re-runs
        # window-scoped SQL for the surviving slots.
        scope = quality.recent_problem_scope()
        snapshot["dq_scope"] = scope
        snapshot["dq_checks"] = (
            quality.run_checks(window=(scope["start"], scope["end"]))
            if scope is not None
            else quality.run_checks()
        )
        snapshot["churn"] = queries.churn_delta(end)
        snapshot["churn_driver"] = queries.churn_driver((start, end), data_end=data_end)
    except Exception as exc:  # noqa: BLE001 - DB problems degrade, never raise
        logger.error("read_state: aggregates failed (%s); degrading snapshot", exc)
        snapshot["degraded"] = True
    return {"snapshot": snapshot, "status": STATUSES["read_state"]}


# --- Node: decide -------------------------------------------------------------


def _pick(answer) -> str | float:
    """The decider's pick for one answer: the argmax option for choice
    (first option wins ties — probabilities arrive in question-option
    order, so a strict `>` keeps the earlier one), "yes"/"no" for noul
    (yes when p >= 0.5), the score itself for score questions. The noul's
    scalar p(yes) ships as-is (issue #17: it reads directly as the
    data_problem probability the DQ card's banner gates on)."""
    probabilities = answer.probabilities
    if isinstance(probabilities, dict):
        best_option, best_value = None, None
        for option, value in probabilities.items():
            if best_value is None or value > best_value:
                best_option, best_value = option, value
        return best_option if best_option is not None else ""
    if answer.type == "score":
        return float(probabilities)
    return "yes" if float(probabilities) >= 0.5 else "no"


async def _decide_with(transport, threshold, question_set):
    """One timed DecisionClient run over the question set — the transport
    is wrapped so every answer carries its call's wall-clock latency."""
    timed = _TimedTransport(transport)
    answers = await decision_client.DecisionClient(
        timed, threshold=threshold
    ).decide(question_set)
    return answers, timed


async def decide(state: AgentState) -> dict:
    """Fan the catalog question set out through DecisionClient.decide and
    stamp one streamed decisions[] entry per answer.

    Off-catalog queries (issue #18: heuristic.classify finds no data domain
    in the message) never consult the wired transport — a fresh guard
    HeuristicTransport answers instead, scoring every catalog option 0.0 so
    the layout cut deterministically empties into the single-insight_text
    fallback no matter what a live model claims.

    Everything else routes through the #8 seam. Any transport failure — or
    a run that parses to zero answers — falls back to a fresh heuristic
    client (which cannot fail), keeping `decisions` a non-empty list of at
    most 7 entries."""
    snapshot = state.get("snapshot") or {}
    if snapshot.get("empty"):
        # Honest-empty: no world was read, so no transport is consulted —
        # the Investigate prefix contract is ignored on this path.
        return {"decisions": [], "status": STATUSES["decide"]}
    user_text = snapshot.get("query") or _last_user_text(state.get("messages") or [])
    question_set = questions.build_question_set(user_text, snapshot)

    base = get_decision_client()
    if classify(user_text) == "off_catalog":
        # Issue #18 safety gate: a live decider misjudging "draw me a
        # dragon" and endorsing kpi_row 0.9 would otherwise render chart
        # tiles for a drawing request. The keyword classifier below is the
        # same table HeuristicTransport itself routes on, so the guard's
        # per-question classification provably lands on the same
        # off-catalog route (every option 0.0) — the cut then keeps nothing
        # and the fallback view renders. Transport I/O is never attempted,
        # so a broken endpoint costs nothing and cannot leak decisions.
        logger.info(
            "decide: %r classified off-catalog; answering with the "
            "deterministic guard transport",
            user_text,
        )
        answers, timed = await _decide_with(
            HeuristicTransport(), base.threshold, question_set
        )
    else:
        try:
            answers, timed = await _decide_with(
                base.transport, base.threshold, question_set
            )
            if not answers:
                # A transport that answered nothing is as broken as one that
                # raised: fall through to the heuristic run below.
                raise ValueError("decision transport returned no parsable answers")
        except Exception as exc:  # noqa: BLE001 - decision failures degrade
            logger.warning(
                "decide: live decision transport failed (%s); falling back to the "
                "heuristic transport",
                exc,
            )
            answers, timed = await _decide_with(
                HeuristicTransport(), base.threshold, question_set
            )

    elapsed_ms = timed.timing_by_id()
    decisions = []
    for answer in answers:
        decisions.append(
            {
                "id": answer.id,
                "type": answer.type,
                "probabilities": answer.probabilities,
                "pick": _pick(answer),
                "model": _decision_model_label(timed, answer.type),
                "latency_ms": round(elapsed_ms.get(answer.id, 0.0), 2),
                "above_threshold": answer.above_threshold,
            }
        )
    return {"decisions": decisions, "status": STATUSES["decide"]}


# --- Node: layout -------------------------------------------------------------


def _layout_threshold() -> float:
    """The endorsement line for the layout cut, read at use time like
    DecisionClient reads it at construction; an unreadable env falls back
    to the default instead of breaking an otherwise runnable graph."""
    raw = os.getenv("DECISION_THRESHOLD")
    if raw is None:
        return decision_client.DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        logger.warning("DECISION_THRESHOLD=%r is not a number; using default", raw)
        return decision_client.DEFAULT_THRESHOLD
    if not 0.0 <= value <= 1.0:
        logger.warning("DECISION_THRESHOLD=%r out of [0, 1]; using default", raw)
        return decision_client.DEFAULT_THRESHOLD
    return value


def _number(value, default=0.0) -> float:
    """JSON number or default — snapshot values are self-produced, but the
    layout node still refuses to build tiles on anything non-numeric."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _snapshot_dict(snapshot, key):
    """snapshot[key] as a dict, or an empty one — degraded snapshots may
    hold nothing, and no builder should ever KeyError over that."""
    value = (snapshot or {}).get(key)
    return value if isinstance(value, dict) else {}


def _kpi_tiles(snapshot, range_label):
    """The four KPI tiles as raw prop dicts, labels embedding the ISO
    window verbatim (props are extra="forbid", so the label is where the
    window lives). Deltas are relative to the previous quarter's snapshot
    values, 0.0 on a zero base; NRR's delta is absolute (a ratio)."""
    kpis = _snapshot_dict(snapshot, "kpis")
    previous = _snapshot_dict(snapshot, "previous_kpis")

    def tile(label, key, absolute=False):
        current = _number(kpis.get(key))
        prior = _number(previous.get(key))
        if absolute:
            delta = current - prior
            return {"label": f"{label} ({range_label})", "value": round(current, 2),
                    "delta": round(delta, 4)}
        # Relative delta against the previous quarter; a zero base reads 0.0
        # (no base, no growth claim).
        delta = (current - prior) / prior if prior else 0.0
        return {"label": f"{label} ({range_label})", "value": round(current, 2),
                "delta": round(delta, 4)}

    tiles = [
        tile("Net-new ARR", "net_new_arr"),
        tile("MRR", "mrr"),
        tile("NRR", "nrr", absolute=True),
    ]
    # Counts stay integers through validation and the wire alike.
    accounts = int(_number(kpis.get("accounts")))
    prior_accounts = int(_number(previous.get("accounts")))
    accounts_delta = (
        accounts - prior_accounts
    ) / prior_accounts if prior_accounts else 0.0
    tiles.append(
        {
            "label": f"Accounts ({range_label})",
            "value": accounts,
            "delta": round(accounts_delta, 4),
        }
    )
    return tiles


def _draft_items(snapshot):
    """A complete, valid draft of every catalog component built from the
    snapshot alone — the threshold cut (not the builders) decides what
    survives, so each builder must produce items that validate on their
    own. Degraded snapshots feed zeros and empty rows, never guesses."""
    window = _snapshot_dict(snapshot, "window")
    range_label = window.get("range") or ""
    churn = _snapshot_dict(snapshot, "churn")
    checks = (snapshot or {}).get("dq_checks")
    if not isinstance(checks, list):
        checks = []
    baseline = _number(churn.get("baseline"))
    return [
        {"type": "kpi_row", "props": {"kpis": _kpi_tiles(snapshot, range_label)}},
        {
            "type": "line_chart",
            "props": {"series": "arr_daily", "range": range_label},
        },
        {
            "type": "variance_callout",
            "props": {
                "metric": "churn rate",
                "delta": round(_number(churn.get("delta")), 4),
                "baseline": f"trailing 6-month average {baseline:.2%}",
                # Issue #16: the data-grounded cause and the Investigate CTA
                # (None off-spike — no invented narrative, no dead button).
                "cause": _churn_cause(_snapshot_dict(snapshot, "churn_driver")),
                "cta": INVESTIGATE_CTA,
            },
        },
        {
            "type": "data_table",
            "props": {
                # Row keys, not display labels: VirtualTable resolves cells
                # by row[column], so display-case columns would render the
                # placeholder for every real row (issue #16).
                "columns": ["segment", "mrr", "tenure_months", "plan"],
                "rows": [],
            },
        },
        {"type": "data_quality_card", "props": {"checks": checks}},
        # Title only: fill writes the narrative into slotCopy, not props.
        {
            "type": "insight_text",
            "props": {"title": f"Insights — {range_label}", "body": ""},
        },
    ]


def _catalog_probabilities(decisions):
    """The catalog choice answer's {component type -> probability} — the
    input to the threshold cut. Missing or malformed answers read as an
    empty map, which drops every component (fail-safe)."""
    for entry in decisions or []:
        if (
            not isinstance(entry, dict)
            or entry.get("id") != questions.CATALOG_QUESTION_ID
        ):
            continue
        probabilities = entry.get("probabilities")
        if not isinstance(probabilities, dict):
            return {}
        return {
            str(key): float(value)
            for key, value in probabilities.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    return {}


def _data_problem_probability(decisions):
    """The data_problem noul's p(yes) — the DQ card's banner gate input
    (issue #17). Tolerant like _catalog_probabilities: missing/malformed
    answers read 0.0 (the gate stays shut, fail-safe) and the scalar is
    clamped into [0, 1] before it reaches the frontend."""
    for entry in decisions or []:
        if (
            not isinstance(entry, dict)
            or entry.get("id") != questions.DATA_PROBLEM_QUESTION_ID
        ):
            continue
        probabilities = entry.get("probabilities")
        if (
            isinstance(probabilities, (int, float))
            and not isinstance(probabilities, bool)
        ):
            return min(1.0, max(0.0, float(probabilities)))
        return 0.0
    return 0.0


def _churn_cause(driver):
    """The variance callout's cause line (issue #16), or None when the data
    does not prove it. The claim is data-grounded: it is spoken ONLY when
    at least half of the cohort's cancelled accounts carry the repricing
    signature (an in-window upgrade before cancelling) — off-spike windows
    get no invented narrative. Fail-safe on a malformed/absent driver (a
    degraded snapshot reads no driver at all)."""
    if not isinstance(driver, dict):
        return None
    cancelled, repriced = driver.get("cancelled"), driver.get("repriced")
    if (
        not isinstance(cancelled, int)
        or isinstance(cancelled, bool)
        or not isinstance(repriced, int)
        or isinstance(repriced, bool)
        or cancelled <= 0
    ):
        return None
    if repriced / cancelled < 0.5:
        return None
    cause = (
        f"Driven by the pricing change: {repriced} of {cancelled} cancelled "
        "accounts had adopted a new plan in the days before cancelling."
    )
    segment, share = driver.get("dominant_segment"), driver.get("dominant_share")
    if isinstance(segment, str) and segment and isinstance(share, (int, float)):
        cause += f" The churn cohort is {share:.0%} {segment}-segment."
    return cause


def _investigate_body(driver):
    """The drill-down insight_text's narrative (issue #16): what the cohort
    table below it shows, grounded in the driver facts — counts, window,
    repriced share and dominant segment. A cohort with no rows states that
    truthfully instead of narrating an empty table."""
    if not isinstance(driver, dict):
        return "No churn cohort data is available for this window."
    rows = driver.get("rows")
    if not isinstance(rows, list) or not rows:
        return "No churn cohort rows matched this window."
    cancelled, repriced = driver.get("cancelled"), driver.get("repriced")
    cohort_start, cohort_end = (
        driver.get("cohort_start"),
        driver.get("cohort_end"),
    )
    body = (
        f"Churn cohort {cohort_start}..{cohort_end}: {cancelled} cancelled "
        f"accounts, {repriced} of them after adopting a new plan."
    )
    segment, share = driver.get("dominant_segment"), driver.get("dominant_share")
    if isinstance(segment, str) and segment and isinstance(share, (int, float)):
        body += f" {share:.0%} of the visible rows are {segment}-segment."
    return body


def _investigate_items(snapshot):
    """The drill-down's two catalog items (issue #16): a data_table whose
    columns are exactly churn_cohort_detail's row keys with a segment filter
    (VirtualTable looks cells up by row[column]), and an insight_text TITLED
    with the cohort — lowercase 'churn cohort' plus the dominant segment, so
    the refreshed a2ui visibly references the cohort it investigates.
    Rows/body stay empty here: the query node fills them per-slot."""
    driver = _snapshot_dict(snapshot, "churn_driver")
    segment = driver.get("dominant_segment")
    if not isinstance(segment, str) or not segment:
        segment = "Churn"
    return [
        {
            "type": "data_table",
            "props": {
                "columns": _INVESTIGATE_COLUMNS,
                "rows": [],
                "filters": ["segment"],
            },
        },
        {
            "type": "insight_text",
            "props": {"title": f"{segment} churn cohort detail", "body": ""},
        },
    ]


def _build_investigate_layout(snapshot) -> dict:
    """The validated a2ui dict for investigate runs (issue #16): the
    drill-down layout replaces the whole canvas for the follow-up question,
    bypassing the threshold cut (its items are CTA-authored, not
    decider-scored). Validation through A2UILayout is the same parse
    assertion _build_layout makes."""
    layout_model = catalog.A2UILayout.model_validate(
        {"items": _investigate_items(snapshot)}
    ).model_dump()
    catalog.A2UILayout.model_validate(layout_model)
    return layout_model


def _build_layout(state: AgentState) -> dict:
    """The validated a2ui dict for the layout node's happy path."""
    snapshot = state.get("snapshot") or {}
    probabilities = _catalog_probabilities(state.get("decisions"))
    kept = catalog.filter_layout(
        _draft_items(snapshot), probabilities, _layout_threshold()
    )
    layout_model = kept.model_dump()
    # Issue #17: stamp the banner gate's probability onto the kept DQ card
    # (not _draft_items — gating is a per-run decision fact, not a draft
    # prop). p prints into the wire props the frontend gates on; a card the
    # cut dropped never carries it. Stamped BEFORE the parse assert so an
    # off-contract stamp fails the node instead of the browser.
    data_problem = _data_problem_probability(state.get("decisions"))
    for item in layout_model["items"]:
        if item.get("type") == "data_quality_card":
            item["props"]["data_problem"] = data_problem
    # Parse assertion (issue acceptance clause 4): what this node stores
    # must survive A2UILayout — the number the frontend trusts.
    catalog.A2UILayout.model_validate(layout_model)
    return layout_model


async def layout(state: AgentState) -> dict:
    """Cut the draft down to the decider's above-threshold picks and store
    the validated result. Sub-threshold components are dropped; when
    nothing survives (off-catalog queries, an unreadable snapshot, any
    build error) the default single-insight_text view takes over.
    Investigate follow-ups (issue #16) never run the cut: their drill-down
    layout is CTA-authored, built and validated directly."""
    snapshot = state.get("snapshot") or {}
    if snapshot.get("empty"):
        # Honest-empty: the degraded snapshot becomes the empty layout —
        # a validated empty-items a2ui, never the single-insight fallback.
        empty_model = catalog.A2UILayout.model_validate({"items": []}).model_dump()
        return {"a2ui": empty_model, "status": STATUSES["layout"]}
    if snapshot.get("investigate"):
        try:
            layout_model = _build_investigate_layout(snapshot)
        except Exception as exc:  # noqa: BLE001 - invalid drafts never break the run
            logger.warning("investigate layout fell back to the default view (%s)", exc)
            layout_model = catalog.fallback_layout().model_dump()
        return {"a2ui": layout_model, "status": STATUSES["layout"]}
    try:
        layout_model = _build_layout(state)
    except Exception as exc:  # noqa: BLE001 - invalid picks never break the run
        logger.warning("layout node fell back to the default view (%s)", exc)
        layout_model = catalog.fallback_layout().model_dump()
    return {"a2ui": layout_model, "status": STATUSES["layout"]}


# --- Node: query --------------------------------------------------------------


_MONTH_ABBRS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _humanize_day_range(start, end):
    """'Sep 8-21, 2026' — the DQ evidence window in banner human terms
    (abbreviated month, ASCII hyphen so the acceptance substring 'Sep'
    matches exactly). Same-year ranges read 'Sep 30-Oct 2, 2026'; a range
    crossing a year boundary keeps both years ('Dec 28, 2025-Jan 3, 2026').
    Raises ValueError on non-ISO bounds (the run's own detection writes
    ISO dates; validation mirrors quality._require_date's discipline)."""
    start_day = date.fromisoformat(_require_iso_day(start, "range start"))
    end_day = date.fromisoformat(_require_iso_day(end, "range end"))
    if start_day > end_day:
        raise ValueError(
            f"range start {start} is after range end {end}"
        )
    if start_day.year != end_day.year:
        return (
            f"{_MONTH_ABBRS[start_day.month - 1]} {start_day.day}, "
            f"{start_day.year}-{_MONTH_ABBRS[end_day.month - 1]} "
            f"{end_day.day}, {end_day.year}"
        )
    if start_day.month != end_day.month:
        return (
            f"{_MONTH_ABBRS[start_day.month - 1]} {start_day.day}-"
            f"{_MONTH_ABBRS[end_day.month - 1]} {end_day.day}, {end_day.year}"
        )
    return (
        f"{_MONTH_ABBRS[start_day.month - 1]} {start_day.day}-"
        f"{end_day.day}, {end_day.year}"
    )


def _require_iso_day(value, label):
    """value must be an ISO 'YYYY-MM-DD' string; returns it verbatim after
    validating (local copy of queries._require_date's contract, so the
    query node never calls date.fromisoformat on unchecked input)."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO 'YYYY-MM-DD' date string")
    date.fromisoformat(value)  # ValueError on anything non-ISO
    return value


def _dq_problem_detail(scope):
    """The DQ banner's evidence line (issue #17): the narrated window and
    segment the planted corruption clusters in — e.g. 'duplicate and null
    usage rows cluster in Sep 8-21, 2026 (2026-09-08..2026-09-21), Startup
    segment'. Total: a malformed or missing scope reads None, never a
    raise (the banner copy is narrated from the agent's own detection)."""
    if not isinstance(scope, dict):
        return None
    start, end = scope.get("start"), scope.get("end")
    try:
        window = f"{_humanize_day_range(start, end)} ({start}..{end})"
    except ValueError:
        return None
    detail = f"duplicate and null usage rows cluster in {window}"
    segment = scope.get("segment")
    if isinstance(segment, str) and segment:
        detail += f", {segment} segment"
    return detail


def _refresh_slot(item, snapshot, driver=None):
    """Re-run one surviving slot's window-scoped SQL in place. Display-time
    props belong to the query's own window, so the three SQL-backed slots
    refresh here — slots whose props are already window-scoped (kpi_row,
    line_chart) need nothing. Exception (issue #17): the DQ card re-runs in
    the detected anomaly scope when there is one, reading where the data is
    actually dirty instead of where the user asked.

    `driver` is the run's churn_driver (issue #16) — the cohort facts the
    data_table/cause/insight refreshes consume; None (a failed or degraded
    run) falls back per-slot: the churn table keeps its window-scoped rows,
    the callout's cause stays None, the drill-down body stays empty/error.
    Investigate runs fill the drill-down table from churn_cohort_detail at
    the cohort window with the dominant segment filter."""
    slot_type = item.get("type")
    props = item.get("props")
    window = _snapshot_dict(snapshot, "window")
    start, end = window.get("start"), window.get("end")
    investigate = bool((snapshot or {}).get("investigate"))
    if not start or not end or not isinstance(props, dict):
        return
    if slot_type == "data_quality_card":
        # Issue #17: the card reads its checks where the corruption lives
        # (the detected anomaly scope) instead of the display window — the
        # display window only matches by coincidence; undetected dirt (or
        # a degraded snapshot) keeps the display-window scoping.
        scope = (snapshot or {}).get("dq_scope")
        if (
            isinstance(scope, dict)
            and isinstance(scope.get("start"), str)
            and isinstance(scope.get("end"), str)
        ):
            props["checks"] = quality.run_checks(
                window=(scope["start"], scope["end"])
            )
            # Compute after the checks so a narrating failure cannot undo
            # the refresh (a None detail just keeps the gate's fallback).
            detail = _dq_problem_detail(scope)
            if detail:
                props["data_problem_detail"] = detail
        else:
            props["checks"] = quality.run_checks(window=(start, end))
    elif slot_type == "data_table":
        if investigate and isinstance(driver, dict):
            props["rows"] = queries.churn_cohort_detail(
                (driver["cohort_start"], driver["cohort_end"]),
                segment=driver.get("dominant_segment"),
                limit=50,
            )
        elif isinstance(driver, dict):
            props["rows"] = driver.get("rows") or []
        else:
            props["rows"] = queries.churn_cohort((start, end), limit=50)
    elif slot_type == "variance_callout":
        churn = queries.churn_delta(end)
        props["delta"] = round(churn["delta"], 4)
        props["baseline"] = f"trailing 6-month average {churn['baseline']:.2%}"
        props["cause"] = _churn_cause(driver)
    elif slot_type == "insight_text" and investigate:
        # Drill-down only: on normal runs fill owns the insight copy (via
        # slotCopy), the props body stays the empty draft.
        props["body"] = _investigate_body(driver)


async def query(state: AgentState) -> dict:
    """Slot-scoped SQL for the surviving slots, merged into `a2ui` and
    re-validated before storing. Per-slot failures (unreadable DB, empty
    window) keep that slot's existing props; a re-validation failure keeps
    the whole pre-query layout — the streamed a2ui never regresses."""
    snapshot = state.get("snapshot") or {}
    if snapshot.get("empty"):
        # Honest-empty: no slots to refresh — the empty a2ui rides through
        # untouched, no churn_driver and no per-slot SQL ever runs.
        return {"status": STATUSES["query"]}
    layout_model = state.get("a2ui")
    if not isinstance(layout_model, dict) or not isinstance(
        layout_model.get("items"), list
    ):
        return {"status": STATUSES["query"]}
    window = _snapshot_dict(snapshot, "window")
    start, end = window.get("start"), window.get("end")
    driver = None
    try:
        driver = queries.churn_driver((start, end), data_end=snapshot.get("data_end"))
    except Exception as exc:  # noqa: BLE001 - one cohort failure keeps prior props
        logger.warning("query: churn_driver failed (%s); slots keep prior props", exc)
    for item in layout_model["items"]:
        if not isinstance(item, dict):
            continue
        try:
            _refresh_slot(item, snapshot, driver)
        except Exception as exc:  # noqa: BLE001 - one slot's SQL failure keeps its props
            logger.warning("query: %s refresh failed (%s)", item.get("type"), exc)
    try:
        # The refresh must re-enter the graph through the schema, never raw.
        catalog.A2UILayout.model_validate(layout_model)
    except ValidationError as exc:
        logger.warning("query: refreshed layout invalid (%s); keeping prior", exc)
        layout_model = state.get("a2ui")
    return {"a2ui": layout_model, "status": STATUSES["query"]}


# --- Node: fill ---------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

# Issue #17: the DQ card's diagnostics slot — machine fact written
# deterministically by fill when the card survived the cut, excluded from
# the model's parsed override (a live model may drop the literal
# 'duplicate'/'null' words the acceptance clause matches on).
DQ_SLOT_KEY = "data_quality_card.summary"


def _parse_loose_json(text):
    """Parse the model's copy brief leniently: strip code fences, then take
    the outermost {...} block. Returns the dict or None — prose, fences and
    preambles all degrade to None instead of raising."""
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()
    while stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```")
        stripped = stripped.removesuffix("```").strip()
    match = _JSON_BLOCK_RE.search(stripped)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _deterministic_copy(snapshot):
    """KPI-grounded copy when the text model is unavailable or unparseable —
    the run stays green and slotCopy stays truthful to the data."""
    window = _snapshot_dict(snapshot, "window")
    range_label = window.get("range") or "the latest complete quarter"
    if snapshot.get("degraded"):
        return {
            "kpis.title": "Data unavailable",
            "insight_text.body": (
                "Live data could not be read for this query, so the canvas shows "
                "placeholders. Try again once the database finishes seeding."
            ),
            "summary": (
                "I could not read the database, so this answer has no live numbers yet."
            ),
        }
    kpis = _snapshot_dict(snapshot, "kpis")
    net_new = _number(kpis.get("net_new_arr"))
    mrr = _number(kpis.get("mrr"))
    nrr = _number(kpis.get("nrr"))
    accounts = int(_number(kpis.get("accounts")))
    churn = _snapshot_dict(snapshot, "churn")
    checks = (snapshot or {}).get("dq_checks")
    if not isinstance(checks, list):
        checks = []
    worst = "ok"
    for row in checks:
        if not isinstance(row, dict):
            continue
        if row.get("status") == "fail":
            worst = "fail"
            break
        if row.get("status") == "warn":
            worst = "warn"
    churn_bits = (
        f" Churn ran {_number(churn.get('delta')):+.1%} against the trailing baseline."
        if churn
        else ""
    )
    return {
        "kpis.title": f"{range_label}: net-new ARR {net_new:,.0f}",
        "line_chart.title": f"ARR trend ({range_label})",
        "insight_text.body": (
            f"Across {range_label}, net-new ARR reached ${net_new:,.0f} with "
            f"MRR at ${mrr:,.0f} and {accounts} active accounts (NRR {nrr:.2f})."
            f"{churn_bits} Data-quality checks read {worst}."
        ),
        "summary": (
            f"Net-new ARR ${net_new:,.0f} for {range_label}; data quality {worst}."
        ),
    }


def _fill_messages(snapshot):
    """The single fill call's prompt: system persona + one copy brief that
    grounds the narrative in the snapshot's real numbers and pins the JSON
    keys the frontend's slotCopy reads."""
    window = _snapshot_dict(snapshot, "window")
    kpis = _snapshot_dict(snapshot, "kpis")
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"The user asked: \"{snapshot.get('query', '')}\"\n"
                f"Display window: {window.get('range', 'unknown')}\n"
                f"KPIs for the window: {json.dumps(kpis, default=str)}\n"
                "Write dashboard copy grounded ONLY in these numbers. Reply with a "
                "JSON object and nothing else, with exactly these keys:\n"
                '- "kpis.title": a headline of at most 8 words about these KPIs;\n'
                '- "line_chart.title": a short chart title of at most 6 words '
                "naming the metric and window;\n"
                '- "insight_text.body": 2-3 sentences of narrative for the insight '
                "card;\n"
                '- "summary": one plain-text sentence for the chat reply.'
            )
        ),
    ]


def _dq_slot_copy(layout_model):
    """The deterministic data_quality_card.summary copy (issue #17): the
    failing checks named verbatim (which carries the literal 'duplicate'
    and 'null' substrings the acceptance clause matches on), or the
    could-not-read variant on empty/missing checks, or the all-passing
    variant otherwise — the narrated detail line appended when the card
    carries one. Returns None when no data_quality_card is in the layout
    (the fallback view) so fill writes no orphan slot key."""
    card = next(
        (
            item
            for item in (layout_model or {}).get("items") or []
            if isinstance(item, dict) and item.get("type") == "data_quality_card"
        ),
        None,
    )
    if card is None:
        return None
    props = card.get("props") or {}
    checks = props.get("checks")
    # Present and non-empty means readable: an empty list (degraded snapshot)
    # or malformed rows are 'could not read', not 'all passing'.
    checks_ok = (
        isinstance(checks, list)
        and len(checks) > 0
        and all(isinstance(row, dict) for row in checks)
    )
    if not checks_ok:
        return "Data-quality checks could not be read for this window."
    failing = [
        str(row.get("name"))
        for row in checks
        if row.get("status") == "fail"
    ]
    detail = props.get("data_problem_detail")
    has_detail = isinstance(detail, str) and bool(detail.strip())
    if failing:
        # Failing names verbatim — the literal 'duplicate'/'null' substrings
        # the acceptance clause matches on ride in those names.
        copy = "Data-quality checks failing: " + ", ".join(failing)
        copy += f" — {detail.strip()}" if has_detail else "."
    else:
        copy = "All data-quality checks pass for this window."
        if has_detail:
            copy += f" — {detail.strip()}"
    return copy


async def _stream_fill_text(snapshot):
    """The fill node's ONE text-model call, streamed chunk-by-chunk so the
    adapter still puts token deltas on the wire (mirrors the old chat
    node's astream-merge loop). Returns the concatenated content, or None
    when the model never produced text."""
    merged: AIMessage | None = None
    async for chunk in build_chat_model().astream(_fill_messages(snapshot)):
        merged = chunk if merged is None else merged + chunk
    if merged is None:
        return None
    content = merged.content
    return content if isinstance(content, str) else json.dumps(content)


async def fill(state: AgentState) -> dict:
    """Exactly one text-model call writes the narrative into slotCopy:
    parsed copy keys override the deterministic defaults, and the "summary"
    becomes the reply AIMessage the chat stream ends on. Prose the parser
    cannot read streams as the summary verbatim; any model failure
    (including the lazy missing-key raise) degrades to the deterministic
    copy, keeping the run — and its streamed a2ui — green."""
    snapshot = state.get("snapshot") or {}
    if snapshot.get("empty"):
        # Honest-empty: the fixed chat string, no model call — the adapter
        # ends the SSE run with RUN_FINISHED carrying this summary. The DQ
        # diagnostics slot is skipped: no data_quality_card survives the
        # empty layout, so there is no key to stamp.
        copy = {"summary": "load a dataset first"}
        layout_model = state.get("a2ui")
        if not isinstance(layout_model, dict):
            layout_model = catalog.A2UILayout.model_validate(
                {"items": []}
            ).model_dump()
        a2ui = {
            **layout_model,
            "slotCopy": {**layout_model.get("slotCopy", {}), **copy},
        }
        try:
            catalog.A2UILayout.model_validate(a2ui)
        except ValidationError as exc:
            logger.warning(
                "fill: copy update invalid (%s); keeping prior layout", exc
            )
            a2ui = layout_model
        return {
            "messages": [AIMessage(content=copy["summary"])],
            "a2ui": a2ui,
            "status": STATUSES["fill"],
        }
    layout_model = state.get("a2ui")
    if not isinstance(layout_model, dict):
        layout_model = catalog.fallback_layout().model_dump()

    text = None
    try:
        text = await _stream_fill_text(snapshot)
    except Exception as exc:  # noqa: BLE001 - the model is optional at run time
        logger.warning(
            "fill: text model unavailable (%s); using deterministic copy", exc
        )

    parsed = _parse_loose_json(text) if text else None
    copy = _deterministic_copy(snapshot)
    if parsed:
        copy.update(
            {
                str(key): value
                for key, value in parsed.items()
                # The DQ summary is machine fact: even a model that emits
                # the key never overrides the deterministic diagnostics.
                if isinstance(value, str) and key != DQ_SLOT_KEY
            }
        )
    elif text and text.strip():
        # Model reply that is not the requested JSON: the chat's closing
        # message is the model's own words verbatim.
        copy["summary"] = text.strip()

    # Issue #17: deterministic diagnostics for a surviving DQ card — the
    # one slot the text model neither writes nor overrides.
    dq_summary = _dq_slot_copy(layout_model)
    if dq_summary is not None:
        copy[DQ_SLOT_KEY] = dq_summary

    a2ui = {
        **layout_model,
        "slotCopy": {**layout_model.get("slotCopy", {}), **copy},
    }
    try:
        catalog.A2UILayout.model_validate(a2ui)
    except ValidationError as exc:
        logger.warning("fill: copy update invalid (%s); keeping prior layout", exc)
        a2ui = layout_model
    return {
        "messages": [AIMessage(content=str(copy.get("summary", "")))],
        "a2ui": a2ui,
        "status": STATUSES["fill"],
    }


# --- Graph assembly -----------------------------------------------------------

builder = StateGraph(AgentState)
builder.add_node("read_state", read_state)
builder.add_node("decide", decide)
builder.add_node("layout", layout)
builder.add_node("query", query)
builder.add_node("fill", fill)
builder.add_edge(START, "read_state")
builder.add_edge("read_state", "decide")
builder.add_edge("decide", "layout")
builder.add_edge("layout", "query")
builder.add_edge("query", "fill")
builder.add_edge("fill", END)

# FastAPI-specific (mirrors the official langgraph-fastapi starter):
# `langgraph dev` supplies its own checkpointer; under uvicorn +
# ag-ui-langgraph the graph needs an explicit MemorySaver for thread
# persistence within a process.
graph = builder.compile(checkpointer=MemorySaver())

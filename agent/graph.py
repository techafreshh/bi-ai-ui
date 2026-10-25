"""bi-ai-ui agent graph: ``read_state → decide → layout → query → fill``.

Pipeline (PRD §6):

- ``read_state`` — routes the query (deterministic keyword classifier) and
  opens the run with a status skeleton.
- ``decide`` — DecisionClient answers the five typed questions for the route;
  decisions reach the wire immediately so the drawer fills first.
- ``layout`` — composes the *skeleton* grid from the component_choice pick
  (placeholder props; components render loading states — skeleton-then-fill).
- ``query`` — runs the whitelisted SQL for the picked components and re-composes
  the layout with real data (narrative still empty; canvas shows data).
- ``fill`` — writes the narrative (text model when a key exists, canned
  per-route copy otherwise), finalises status (fallback/warnings) and returns
  the assistant reply message.

State/wire contract: ``AgentState`` carries exactly ``a2ui``, ``decisions``,
``status`` over ``MessagesState``. Every node emits the FULL triple via
``copilotkit_emit_state`` (ag-ui ``STATE_SNAPSHOT`` replaces frontend state,
so partial emits would blank the drawer). The final runtime snapshot carries
the complete checkpoint state, which is what ``scripts/verify_demo.py`` and
the React canvas both consume.
"""

import logging
import os
from functools import reduce
from typing import Any

from copilotkit.langgraph import copilotkit_emit_state
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

import catalog
import quality
import queries
import routes
import store
from decision.client import LOCAL_POLICY_MODEL, DecisionClient
from routes import RouteId

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TEXT_MODEL = "openai/gpt-6-luna"

# Noul probability above which the DQ route raises a warning banner (issue
# acceptance: noul p>0.7).
NOUL_BANNER = 0.7

SYSTEM_PROMPT = (
    "You are the bi-ai-ui assistant, a business-intelligence chat agent. "
    "You answer by composing views from a fixed component catalog; write the "
    "narrative copy for the assembled view in 2–3 concise sentences."
)


class MissingKeyError(RuntimeError):
    """OPENROUTER_API_KEY is absent; narrative streaming can't run (server still boots)."""


# Built lazily on first narrative call so a missing key can never break module
# import or the /health endpoint.
_chat_model: ChatOpenAI | None = None


def get_text_model() -> ChatOpenAI:
    """Return the OpenRouter-backed chat model, building it on first use.

    Raises:
        MissingKeyError: if OPENROUTER_API_KEY is not set.
    """
    global _chat_model
    if _chat_model is not None:
        return _chat_model
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise MissingKeyError(
            "OPENROUTER_API_KEY is not set (agent/.env). Copy .env.example to "
            "agent/.env, add a key, and restart the agent."
        )
    _chat_model = ChatOpenAI(
        model=os.getenv("TEXT_MODEL", DEFAULT_TEXT_MODEL),
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        streaming=True,
    )
    return _chat_model


def reset_text_model() -> None:
    """Drop the cached model so the next call rebuilds it (used by tests)."""
    global _chat_model
    _chat_model = None


class AgentState(MessagesState):
    """Graph state; the non-message keys are the wire contract with the SPA."""

    a2ui: dict[str, Any] | None
    decisions: list[dict[str, Any]]
    status: dict[str, Any]


async def _emit(config: RunnableConfig, a2ui: dict | None, decisions: list[dict],
                status: dict) -> None:
    """Push a full state snapshot down the AG-UI wire (best effort).

    Outside a streaming runtime (unit tests call nodes functionally) custom
    events have nowhere to go — swallow and continue rather than fail the run.
    """
    try:
        await copilotkit_emit_state(config, {"a2ui": a2ui, "decisions": decisions, "status": status})
    except Exception as exc:  # noqa: BLE001 — no runtime to dispatch into
        logger.debug("state emit skipped (no streaming runtime): %s", exc)


def _last_user_text(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _gather_evidence() -> dict[str, Any]:
    """Measured quantities the decisions are about (cheap, deterministic)."""
    conn = store.connect()
    try:
        return {
            "dq": quality.dq_checks(conn),
            "var": queries.churn_variance(conn),
            "q3": queries.q3_kpis(conn)["summary"],
        }
    finally:
        conn.close()


def _node_status(state: AgentState, **updates: Any) -> dict:
    """Progressively refined status object shared by all nodes."""
    status = dict(state.get("status") or {})
    status.update(updates)
    if "a2ui" in state and "fallback" not in status:
        status["fallback"] = False
    return status


def _placeholder_props(component: str) -> dict[str, Any]:
    """Safe-empty props for skeleton items (components render loading states)."""
    return {
        "kpi_row": {"kpis": []},
        "line_chart": {"series": "arr_daily", "range": "", "points": []},
        "variance_callout": {"metric": "", "delta": 0, "cause": ""},
        "data_table": {"columns": [], "rows": []},
        "data_quality_card": {"checks": []},
        "insight_text": {"title": "", "body": ""},
    }[component]


def _picks(decisions: list[dict]) -> list[str]:
    choice = next((d for d in decisions if d.get("id") == "component_choice"), None)
    return list((choice or {}).get("pick") or [])


def _compose(route: RouteId, decisions: list[dict], narrative: str) -> tuple[dict, dict]:
    """Compose + validate a data-complete layout (props filled by ``_props_for``).

    Only used by query/fill, where every picked component has data. Components
    whose props were never computed are dropped (compose meta lists them).
    """
    props: dict[str, dict[str, Any]] = {}
    for component in _picks(decisions):
        built = _props_for(component, route)
        if built is not None:
            props[component] = built
    return catalog.compose_layout(route, decisions, props, narrative)


def _props_for(component: str, route: RouteId) -> dict[str, Any] | None:
    """Query the whitelisted SQL for one component's props (or None if n/a)."""
    conn = store.connect()
    try:
        if component == "kpi_row" and route in ("investigate_churn", "churn_why"):
            var = queries.churn_variance(conn)
            cohort = queries.churn_cohort(conn)["rows"]
            avg_tenure = round(sum(r["tenure_months"] for r in cohort) / len(cohort), 1) if cohort else 0
            return {"kpis": [
                {"label": "Churned accounts", "value": len(cohort), "delta": None},
                {"label": "Churned MRR (Aug–Sep)", "value": var["spike_total"], "delta": var["delta"]},
                {"label": "Avg tenure (months)", "value": avg_tenure, "delta": None},
            ]}
        if component == "kpi_row":
            return queries.q3_kpis(conn)
        if component == "line_chart":
            return queries.arr_daily_series(conn, store.Q3_START.isoformat(), store.Q3_END.isoformat())
        if component == "variance_callout":
            var = dict(queries.churn_variance(conn))
            var["cta"] = "investigate_churn"
            return var
        if component == "data_table":
            return queries.churn_cohort(conn)
        if component == "data_quality_card":
            return quality.dq_checks(conn)
        # insight_text props are pure narrative, filled by the fill node.
        if component == "insight_text":
            return {"title": "Narrative", "body": ""}
        return None
    finally:
        conn.close()


def _mode(decisions: list[dict]) -> str:
    return "demo-local" if all(d.get("model") == LOCAL_POLICY_MODEL for d in decisions) else "live"


def _canned_narrative(route: RouteId, evidence: dict[str, Any]) -> str:
    """Deterministic per-route copy for demo mode (no API key needed)."""
    var = evidence.get("var", {})
    dq = evidence.get("dq", {})
    q3 = evidence.get("q3", {})
    if route == "q3_sales":
        return (
            f"Q3 net-new ARR was ${q3.get('net_new_arr_q3', 0):,} — the Aug 1 campaign "
            f"roughly tripled daily bookings, closing MRR at ${q3.get('closing_mrr', 0):,}. "
            f"Net revenue retention eased to {q3.get('nrr_q3', 0):.3f} because the same "
            "quarter carries the Growth-tier churn spike."
        )
    if route in ("churn_why", "investigate_churn"):
        return (
            f"Churned MRR ran {var.get('delta', 0) * 100:.0f}% above the trailing "
            f"baseline (${var.get('baseline_monthly', 0):,}/month → "
            f"${var.get('spike_monthly', 0):,}/month). The spike starts at the Aug 10 "
            "pricing change: 42 Growth-segment accounts on the "
            "legacy plan cancelled through September."
        )
    if route == "data_clean":
        failed = [c["name"] for c in dq.get("checks", []) if c["status"] == "fail"]
        return (
            f"Two of three checks fail ({', '.join(failed) or 'none'}). The Sept ingest bug "
            f"({dq.get('bug_window', '')}) duplicated and nulled Startup usage amounts — "
            f"null share {dq.get('null_ratio', 0) * 100:.1f}% against a 2% breach threshold. "
            "Freshness is green, so current data keeps flowing; the Startup figures inside "
            "the bug window are the suspect ones."
        )
    if route == "report_issue":
        return (
            "Logged. I filed the issue against the failing data-quality checks "
            "(duplicates, null amounts in the Sept ingest window); the DQ card stays "
            "evidence in the dashboard until the ingest job is re-run."
        )
    return "I can only compose views from the dashboard catalog — try one of the suggested business questions."


async def read_state(state: AgentState, config: RunnableConfig) -> dict:
    """Route the query and open the run."""
    text = _last_user_text(state)
    route = routes.classify_route(text)
    status = _node_status(state, stage="reading", route=route, query=text[:200])
    await _emit(config, None, [], status)
    return {"a2ui": None, "decisions": [], "status": status}


async def decide(state: AgentState, config: RunnableConfig) -> dict:
    """Answer the five typed questions for the route; drawer data hits the wire first."""
    route = state.get("status", {}).get("route", "off_catalog")
    evidence = _gather_evidence()
    questions = routes.build_questions(route)
    decisions = DecisionClient().run(route, questions, evidence)
    status = _node_status(state, stage="deciding", mode=_mode(decisions),
                          decision_model=decisions[0]["model"] if decisions else None)
    await _emit(config, None, decisions, status)
    return {"a2ui": None, "decisions": decisions, "status": status}


async def layout(state: AgentState, config: RunnableConfig) -> dict:
    """Skeleton grid from the decision picks (placeholder props, unvalidated)."""
    route = state.get("status", {}).get("route", "off_catalog")
    decisions = state.get("decisions", [])
    picked = _picks(decisions) or ["insight_text"]  # off-catalog: fallback card right away
    a2ui = {
        "v": 1,
        "layout": "grid",
        "route": route,
        "items": [{"type": c, "props": _placeholder_props(c)} for c in picked],
        "slotCopy": {},
    }
    status = _node_status(state, stage="layout")
    await _emit(config, a2ui, decisions, status)
    return {"a2ui": a2ui, "decisions": decisions, "status": status}


async def query(state: AgentState, config: RunnableConfig) -> dict:
    """Load props for the picked components and re-compose with real data."""
    route = state.get("status", {}).get("route", "off_catalog")
    decisions = state.get("decisions", [])
    a2ui, meta = _compose(route, decisions, narrative="")
    status = _node_status(state, stage="querying", dropped=meta["dropped"])
    await _emit(config, a2ui, decisions, status)
    return {"a2ui": a2ui, "decisions": decisions, "status": status}


async def fill(state: AgentState, config: RunnableConfig) -> dict:
    """Write the narrative, finalise status, and return the reply message."""
    route = state.get("status", {}).get("route", "off_catalog")
    decisions = state.get("decisions", [])
    evidence = _gather_evidence()
    narrative, reply_prefix = await _narrative(route, evidence, decisions)
    a2ui, meta = _compose(route, decisions, narrative=narrative)

    warnings = _warnings(route, decisions)
    status = _node_status(state, stage="done", fallback=meta["fallback"],
                          picked_components=meta["picked"], dropped=meta["dropped"],
                          warnings=warnings)
    reply = _reply(reply_prefix, narrative, a2ui)
    await _emit(config, a2ui, decisions, status)
    return {"a2ui": a2ui, "decisions": decisions, "status": status, "messages": [reply]}


def _warnings(route: RouteId, decisions: list[dict]) -> list[str]:
    """Banner strings when the data says the numbers are suspect (noul p>0.7)."""
    warnings: list[str] = []
    if route == "data_clean":
        noul = next((d for d in decisions if d.get("id") == "real_vs_data_problem"), {})
        p = (noul.get("probabilities") or {}).get("p", 0)
        if p > NOUL_BANNER:
            warnings.append(
                f"Data problem likely (p={p:.2f}); Startup usage in the Sept window is suspect."
            )
    return warnings


def _reply(prefix: str, narrative: str, a2ui: dict) -> AIMessage:
    """Chat reply: demo-mode prefix, assembled-component pills, narrative.

    The pill list is the lightweight "mini-card in chat" (PRD §4): the reply
    names what assembled while the canvas renders the full components.
    """
    types = [item["type"] for item in a2ui.get("items", [])]
    labels = " · ".join(catalog.COMPONENT_LABELS.get(t, t) for t in types)
    return AIMessage(content=f"{prefix}Assembled: **{labels}**\n\n{narrative}")


async def _narrative(route: RouteId, evidence: dict[str, Any],
                     decisions: list[dict]) -> tuple[str, str]:
    """(narrative, reply_prefix) — streamed text model, canned copy otherwise.

    Missing key or model failure degrades to the deterministic canned copy with
    a ``⚠``-prefixed reply (the walking-skeleton no-crash contract).
    """
    canned = _canned_narrative(route, evidence)
    try:
        model = get_text_model()
    except MissingKeyError as exc:
        logger.warning("narrative skipped: %s", exc)
        return canned, f"⚠️ Demo mode (live narrative off: {exc}).\n\n"
    prompt = _narrative_prompt(route, evidence, decisions)
    try:
        messages = [SystemMessage(content=SYSTEM_PROMPT), SystemMessage(content=prompt),
                    HumanMessage(content="Write the narrative for this assembled view.")]
        chunks = [chunk async for chunk in model.astream(messages)]
    except Exception as exc:  # noqa: BLE001 — live narrative must never fail the run
        logger.warning("narrative streaming failed, using canned copy: %s", exc)
        return canned, "⚠️ "
    if not chunks:
        return canned, "⚠️ "
    return reduce(lambda a, b: a + b, chunks).content, ""


def _narrative_prompt(route: RouteId, evidence: dict[str, Any], decisions: list[dict]) -> str:
    facts: dict[str, Any] = {"route": route}
    if route == "q3_sales":
        facts.update({"kpis": evidence.get("q3"), "churn_delta": evidence.get("var", {}).get("delta")})
    elif route in ("churn_why", "investigate_churn"):
        facts.update({"variance": evidence.get("var"), "dq": evidence.get("dq", {}).get("checks")})
    elif route == "data_clean":
        facts.update({"dq": evidence.get("dq")})
    return (
        "Assembled-view facts (JSON). Only these numbers, no invention:\n"
        + str(facts)
    )


def build_graph() -> CompiledStateGraph:
    """Compile the five-node graph.

    The explicit MemorySaver is required: under uvicorn + ag-ui-langgraph
    there is no langgraph-cli checkpointer supplying one, and AG-UI runs carry
    a thread_id that LangGraph refuses without a checkpointer.
    """
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
    return builder.compile(checkpointer=MemorySaver())

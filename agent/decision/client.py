"""DecisionClient: typed questions → DecisionRecords via pluggable transports.

Why a transport chain (PRD §8/§14): the OpenRouter decisions-modality contract
is genuinely unverified, so the client tries, in order:

1. ``local`` — the deterministic per-route policy table below. Zero network,
   zero cost, byte-stable across runs; this is what the probe suite and unit
   tests exercise and what the demo falls back to without an API key.
2. ``openrouter_decisions`` — the decisions endpoint against ``DECISION_MODEL``.
3. ``openrouter_chat`` — chat completions with a JSON-schema response format.
4. back to ``local`` for anything the upstream transports failed to answer.

``DECISION_TRANSPORT`` selects: ``auto`` (default) = chain when
OPENROUTER_API_KEY is set, else local; ``live`` = always chain (still ends in
local); ``mock`` = always local (tests / offline CI).

Decision record shape (the single contract consumed by drawer and probes):
``{id, type, question, options, probabilities, pick, value, threshold, model,
latency_ms, passed}`` — ``latency_ms`` is the measured transport/compute time
and ``model`` names what actually produced the answer.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from routes import Question, RouteId

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_POLICY_MODEL = "demo-local-policy"
DEFAULT_DECISION_MODEL = "~perplexity/pplx-decider-flash-latest"
CHAT_FALLBACK_MODEL = "anthropic/claude-haiku-5.5"
REQUEST_TIMEOUT_S = 2.5  # PRD §14: resolves to fallbacks fast, never hangs the demo


class DecisionError(RuntimeError):
    """A live transport failed (caller falls through the chain)."""


# Deterministic per-route probability tables. Keyed
# [route][question_id] -> {option: p} (choice) or scalar p (noul/score).
# Values only ever feed the drawer/probes; component routing applies
# DECISION_THRESHOLD on top.
_POLICY: dict[str, dict[str, Any]] = {
    "q3_sales": {
        "component_choice": {
            "kpi_row": 0.91, "line_chart": 0.88, "variance_callout": 0.22,
            "data_table": 0.28, "data_quality_card": 0.18, "insight_text": 0.55,
        },
        "real_vs_data_problem": 0.15,
        "anomaly_callout": 0.32,
        "dq_confidence": 0.92,
        "granularity": {"month": 0.86, "week": 0.38, "quarter": 0.24, "account": 0.12},
    },
    "churn_why": {
        "component_choice": {
            "kpi_row": 0.24, "line_chart": 0.31, "variance_callout": 0.93,
            "data_table": 0.90, "data_quality_card": 0.20, "insight_text": 0.54,
        },
        "real_vs_data_problem": 0.18,
        "anomaly_callout": 0.87,
        "dq_confidence": 0.84,
        "granularity": {"account": 0.91, "week": 0.36, "month": 0.30, "quarter": 0.14},
    },
    "data_clean": {
        "component_choice": {
            "kpi_row": 0.14, "line_chart": 0.12, "variance_callout": 0.30,
            "data_table": 0.22, "data_quality_card": 0.95, "insight_text": 0.64,
        },
        "real_vs_data_problem": 0.94,  # evidence-driven below
        "anomaly_callout": 0.12,
        "dq_confidence": 0.35,  # evidence-driven below
        "granularity": {"check": 0.88, "event": 0.31, "segment": 0.27, "day": 0.10},
    },
    "investigate_churn": {
        "component_choice": {
            "kpi_row": 0.70, "line_chart": 0.42, "variance_callout": 0.35,
            "data_table": 0.94, "data_quality_card": 0.15, "insight_text": 0.66,
        },
        "real_vs_data_problem": 0.20,
        "anomaly_callout": 0.41,
        "dq_confidence": 0.80,
        "granularity": {"account": 0.94, "day": 0.30, "week": 0.26, "segment": 0.12},
    },
    "report_issue": {
        "component_choice": {
            "kpi_row": 0.10, "line_chart": 0.10, "variance_callout": 0.15,
            "data_table": 0.12, "data_quality_card": 0.55, "insight_text": 0.90,
        },
        "real_vs_data_problem": 0.55,
        "anomaly_callout": 0.30,
        "dq_confidence": 0.45,
        "granularity": {"summary": 0.72, "check": 0.30, "event": 0.18, "account": 0.10},
    },
    "off_catalog": {
        "component_choice": {
            "kpi_row": 0.35, "line_chart": 0.30, "variance_callout": 0.25,
            "data_table": 0.28, "data_quality_card": 0.18, "insight_text": 0.45,
        },
        "real_vs_data_problem": 0.49,
        "anomaly_callout": 0.40,
        "dq_confidence": 0.75,
        "granularity": {"summary": 0.72, "detailed": 0.20, "list": 0.10, "table": 0.06},
    },
}


def default_threshold() -> float:
    return float(os.getenv("DECISION_THRESHOLD", "0.6"))


def resolve_transport() -> str:
    """Effective transport name from DECISION_TRANSPORT + key presence."""
    mode = os.getenv("DECISION_TRANSPORT", "auto").strip().lower()
    if mode == "mock":
        return "local"
    if mode == "live":
        return "chain"
    return "chain" if os.getenv("OPENROUTER_API_KEY") else "local"


def local_answers(route: RouteId, evidence: dict[str, Any]) -> dict[str, Any]:
    """Per-route probabilities, with the data-driven overrides applied.

    Two answers read measured evidence instead of the static table so the
    drawer shows honest numbers: on the data_clean route the data-problem
    belief and the DQ confidence react to the actual duplicate/null counts.
    """
    table = {qid: dict(values) if isinstance(values, dict) else values
             for qid, values in _POLICY[route].items()}
    dq = evidence.get("dq") or {}
    failing = (dq.get("dup_groups") or 0) > 0 or dq.get("null_ratio", 0.0) > 0.02
    if route == "data_clean":
        table["real_vs_data_problem"] = 0.94 if failing else 0.25
        table["dq_confidence"] = 0.35 if failing else 0.85
    return table


def evidence_value(question_id: str, route: RouteId, evidence: dict[str, Any]) -> float | None:
    """The measured quantity a noul/score decision is about (always from data)."""
    dq = evidence.get("dq") or {}
    var = evidence.get("var") or {}
    q3 = evidence.get("q3") or {}
    if question_id == "real_vs_data_problem":
        if route == "data_clean":
            return round(dq.get("null_ratio", 0.0), 4)
        if route == "churn_why" or route == "investigate_churn":
            return var.get("delta")
        if route == "q3_sales":
            net = q3.get("net_new_arr_q3")
            return round(net / 1000.0, 2) if net is not None else None
        return round(dq.get("null_ratio", 0.0), 4)
    if question_id == "anomaly_callout":
        return var.get("delta")
    if question_id == "dq_confidence":
        return dq.get("null_ratio")
    return None


def build_record(
    question: Question,
    probabilities: dict[str, float] | float,
    threshold: float,
    model: str,
    latency_ms: float,
    value: float | None,
) -> dict:
    """Single place the canonical decision-record shape is built.

    ``passed`` semantics by type:
    - choice: at least one option cleared the threshold (components survived)
    - noul: pick is the affirmative option (data_problem / anomaly)
    - score: value cleared the threshold
    """
    if isinstance(probabilities, dict):
        p_by_option = {o: float(probabilities.get(o, 0.0)) for o in question.options}
        pick = [o for o in question.options if p_by_option[o] >= threshold]
        passed = len(pick) > 0
        probs_out = {o: round(p, 4) for o, p in p_by_option.items()}
        pick_out: Any = list(pick)
    else:
        p = float(probabilities)
        probs_out = {"p": round(p, 4)}
        if question.type == "noul":
            pick_out = question.options[0] if p >= 0.5 else question.options[1]
            passed = pick_out == question.options[0]
        else:  # score
            pick_out = None
            passed = p >= threshold
    return {
        "id": question.id,
        "type": question.type,
        "question": question.prompt,
        "options": list(question.options),
        "probabilities": probs_out,
        "pick": pick_out,
        "value": round(value, 4) if isinstance(value, (int, float)) else value,
        "threshold": threshold,
        "model": model,
        "latency_ms": round(float(latency_ms), 2),
        "passed": passed,
    }


def local_decisions(route: RouteId, questions: list[Question], evidence: dict[str, Any],
                    threshold: float) -> list[dict]:
    """Deterministic records straight from the policy table."""
    table = local_answers(route, evidence)
    records = []
    for q in questions:
        started = time.perf_counter()
        probabilities = table[q.id]
        rec = build_record(q, probabilities, threshold, LOCAL_POLICY_MODEL,
                           (time.perf_counter() - started) * 1000.0,
                           evidence_value(q.id, route, evidence))
        records.append(rec)
    return records


def _clamp01(p: float) -> float:
    return max(0.0, min(1.0, float(p)))


def _normalize_answers(raw_answers: list[dict], questions: list[Question],
                       threshold: float, model: str, total_ms: float,
                       evidence: dict[str, Any], route: RouteId) -> list[dict]:
    """Convert upstream answers into canonical records; fill gaps from policy.

    Raising any error here would waste a successful upstream call, so unknown
    ids/shapes are skipped and only *missing* questions fall back to local.
    """
    answered: dict[str, Any] = {}
    for ans in raw_answers:
        qid = ans.get("id")
        if qid in {q.id for q in questions}:
            answered[qid] = ans
    per_ms = total_ms / max(len(questions), 1)
    records = []
    for q in questions:
        probs: Any = None
        ans = answered.get(q.id)
        if ans is not None:
            raw = ans.get("probabilities")
            if isinstance(raw, dict):
                # Upstream may answer with probabilities for known options only.
                clamped = {o: _clamp01(p) for o, p in raw.items() if o in q.options}
                if q.options and any(p > 0 for p in clamped.values()):
                    probs = clamped  # else unusable: fall through to policy
            elif isinstance(raw, (int, float)):
                probs = _clamp01(raw)
        if probs is None:
            # Missing or unusable upstream answer: this question (only)
            # falls back to the deterministic local policy.
            records.append(local_decisions(route, [q], evidence, threshold)[0])
        else:
            records.append(build_record(q, probs, threshold, model, per_ms,
                                        evidence_value(q.id, route, evidence)))
    return records


DECISIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "probabilities": {
                        "description": "Map of option->probability for choice questions, {\"p\": x} for noul/score.",
                        "type": "object",
                    },
                },
                "required": ["id", "probabilities"],
            },
        },
    },
    "required": ["answers"],
}


def _post_openrouter(api_key: str, path: str, payload: dict, timeout: float) -> dict:
    url = f"{OPENROUTER_BASE_URL}{path}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
    if not isinstance(body, dict):
        raise DecisionError("non-object response body")
    return body


def _openrouter_decisions(questions: list[Question], evidence: dict[str, Any],
                          model: str, api_key: str, threshold: float,
                          route: RouteId, timeout: float) -> list[dict]:
    """Transport 2: the decisions-modality endpoint (PRD §10 wire shape)."""
    payload = {
        "model": model,
        "state": {"route": route, "evidence": _compact_evidence(evidence)},
        "questions": [
            {"id": q.id, "type": q.type, "prompt": q.prompt, "options": list(q.options)}
            for q in questions
        ],
    }
    started = time.perf_counter()
    body = _post_openrouter(api_key, "/decisions", payload, timeout)
    total_ms = (time.perf_counter() - started) * 1000.0
    answers = body.get("answers")
    if not isinstance(answers, list):
        raise DecisionError("decisions endpoint returned no answers list")
    return _normalize_answers(answers, questions, threshold, model, total_ms, evidence, route)


def _openrouter_chat(questions: list[Question], evidence: dict[str, Any],
                     model: str, api_key: str, threshold: float,
                     route: RouteId, timeout: float) -> list[dict]:
    """Transport 3: chat completions with response_format json_schema."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You answer probabilistic UI-decision questions. Reply only with the JSON schema shape: {\"answers\": [{\"id\", \"probabilities\"}]}."},
            {"role": "user", "content": json.dumps({
                "route": route,
                "evidence": _compact_evidence(evidence),
                "questions": [
                    {"id": q.id, "type": q.type, "prompt": q.prompt, "options": list(q.options)}
                    for q in questions
                ],
            })},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "decisions", "strict": True, "schema": DECISIONS_SCHEMA},
        },
    }
    started = time.perf_counter()
    body = _post_openrouter(api_key, "/chat/completions", payload, timeout)
    total_ms = (time.perf_counter() - started) * 1000.0
    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        answers = parsed["answers"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DecisionError(f"chat structured-output parse failed: {exc}") from exc
    if not isinstance(answers, list):
        raise DecisionError("chat structured output missing answers list")
    return _normalize_answers(answers, questions, threshold, model, total_ms, evidence, route)


def _compact_evidence(evidence: dict[str, Any]) -> dict:
    """Small snapshot so upstream calls stay cheap; never raw rows."""
    dq = evidence.get("dq") or {}
    return {
        "dq": {"null_ratio": dq.get("null_ratio"), "dup_groups": dq.get("dup_groups"),
               "checks": dq.get("checks")},
        "var": {k: evidence.get("var", {}).get(k) for k in ("metric", "delta", "baseline_monthly", "spike_monthly")},
        "q3": evidence.get("q3") or {},
    }


class DecisionClient:
    """Facade the graph's ``decide`` node calls once per query."""

    def __init__(self, transport: str | None = None, threshold: float | None = None):
        self.threshold = threshold if threshold is not None else default_threshold()
        self.requests_timeout = float(os.getenv("DECISION_TIMEOUT_S", str(REQUEST_TIMEOUT_S)))
        self._transport = transport  # None = resolve from env

    def run(self, route: RouteId, questions: list[Question],
            evidence: dict[str, Any]) -> list[dict]:
        """Answer all questions for one query; never raises (falls back to local)."""
        transport = self._transport or resolve_transport()
        if transport != "chain":
            return local_decisions(route, questions, evidence, self.threshold)
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.warning("live transport requested without OPENROUTER_API_KEY; using local policy")
            return local_decisions(route, questions, evidence, self.threshold)
        model = os.getenv("DECISION_MODEL", DEFAULT_DECISION_MODEL)
        for attempt in (self._decisions_endpoint, self._chat_structured):
            try:
                return attempt(questions, evidence, model, api_key, route)
            except Exception as exc:  # noqa: BLE001 — any transport failure falls through
                logger.warning("decision transport %s failed: %s", attempt.__name__, exc)
        return local_decisions(route, questions, evidence, self.threshold)

    def _decisions_endpoint(self, questions, evidence, model, api_key, route) -> list[dict]:
        return _openrouter_decisions(questions, evidence, model, api_key,
                                     self.threshold, route, self.requests_timeout)

    def _chat_structured(self, questions, evidence, model, api_key, route) -> list[dict]:
        chat_model = os.getenv("DECISION_CHAT_MODEL", CHAT_FALLBACK_MODEL)
        return _openrouter_chat(questions, evidence, chat_model, api_key,
                                self.threshold, route, self.requests_timeout)

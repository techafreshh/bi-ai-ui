#!/usr/bin/env python3
"""Black-box probe suite over the RUNNING bi-ai-ui stack (PRD §11 checklist).

Exercises the real AG-UI path — the same one the React canvas consumes — by
POSTing a RunAgentInput to the agent's /copilotkit endpoint and reading the
SSE stream: no extra endpoints, no test backdoors.

Stdlib only (urllib + json): runs on a bare python3 on any machine that has
the stack up.

Usage:
    python3 scripts/verify_demo.py            # after `docker compose up`

Env overrides: AGENT_URL (default http://localhost:8123), WEB_URL
(default http://localhost:5173), VERIFY_TIMEOUT_S (per-run SSE read limit).

Each check prints ``[ok] name: detail`` or ``[FAIL] name: detail``; the exit
code is 0 only when every check passes. Key-independent by design: the demo
runs in deterministic local mode without OPENROUTER_API_KEY, and these probes
never require network egress.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8123").rstrip("/")
WEB_URL = os.getenv("WEB_URL", "http://localhost:5173").rstrip("/")
RUN_TIMEOUT_S = float(os.getenv("VERIFY_TIMEOUT_S", "60"))
STACK_WAIT_S = float(os.getenv("VERIFY_STACK_WAIT_S", "60"))

Q3_QUERY = "Show me sales in Q3"
CHURN_QUERY = "Why did churn spike in August?"
DQ_QUERY = "Is the data clean?"
OFF_CATALOG_QUERY = "Draw me a dragon"

CATALOG_TYPES = {
    "kpi_row", "line_chart", "variance_callout",
    "data_table", "data_quality_card", "insight_text",
}

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> bool:
    marker = "ok  " if ok else "FAIL"
    print(f"[{marker}] {name}: {detail}")
    if not ok:
        failures.append(name)
    return ok


def http_get(url: str, timeout: float = 10.0, headers: dict | None = None) -> tuple[int, str, dict]:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, "", dict(exc.headers or {})


def run_query_sse(query: str) -> dict:
    """One agent run over the real /copilotkit AG-UI SSE endpoint.

    Returns the tracked {a2ui, decisions, status, reply, event_types} view.
    STATE_SNAPSHOT replaces the whole frontend state, so keys are tracked
    independently — last value wins per key present.
    """
    body = {
        "threadId": str(uuid.uuid4()),
        "runId": str(uuid.uuid4()),
        "state": {},
        "messages": [
            {"id": str(uuid.uuid4()), "role": "user", "content": query}
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    request = urllib.request.Request(
        f"{AGENT_URL}/copilotkit",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    tracked: dict = {"a2ui": None, "decisions": [], "status": {}, "reply": ""}
    types: list[str] = []
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=RUN_TIMEOUT_S) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            types.append(etype)
            snapshot = event.get("snapshot")
            if isinstance(snapshot, dict):
                for key in ("a2ui", "decisions", "status"):
                    if key in snapshot:
                        tracked[key] = snapshot[key]
            if etype == "MESSAGES_SNAPSHOT" and not tracked["reply"]:
                for message in reversed(event.get("messages") or []):
                    if message.get("role") == "assistant":
                        tracked["reply"] = str(message.get("content") or "")
                        break
            if etype in ("RUN_FINISHED", "RUN_ERROR") and time.monotonic() - started > RUN_TIMEOUT_S:
                break
    tracked["event_types"] = types
    return tracked


def items_of(run: dict) -> list[dict]:
    a2ui = run.get("a2ui") or {}
    return a2ui.get("items") or []


def item_by_type(run: dict, component: str) -> dict | None:
    for item in items_of(run):
        if item.get("type") == component:
            return item
    return None


def decision_by_id(run: dict, question_id: str) -> dict | None:
    for record in run.get("decisions") or []:
        if record.get("id") == question_id:
            return record
    return None


def decisions_are_observable(run: dict) -> tuple[bool, str]:
    decisions = run.get("decisions") or []
    if len(decisions) < 4:
        return False, f"only {len(decisions)} decisions recorded"
    for record in decisions:
        if not record.get("model"):
            return False, f"{record.get('id')}: missing model"
        if not isinstance(record.get("latency_ms"), (int, float)):
            return False, f"{record.get('id')}: missing latency_ms"
        if "probabilities" not in record:
            return False, f"{record.get('id')}: missing probabilities"
    return True, f"{len(decisions)} decisions with model+latency+probabilities"


def run_finished_cleanly(run: dict, label: str) -> tuple[bool, str]:
    types = run.get("event_types") or []
    if "RUN_ERROR" in types:
        return False, f"{label}: RUN_ERROR on the wire"
    if "RUN_FINISHED" not in types:
        return False, f"{label}: stream ended without RUN_FINISHED"
    return True, f"{label}: RUN_FINISHED, no errors"


def main() -> int:
    # -- wait for the stack ------------------------------------------------
    deadline = time.monotonic() + STACK_WAIT_S
    agent_up = False
    while time.monotonic() < deadline:
        try:
            status, body, _ = http_get(f"{AGENT_URL}/health", timeout=3.0)
            if status == 200 and json.loads(body).get("status") == "ok":
                agent_up = True
                break
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(1.0)
    if not agent_up:
        print(f"[FAIL] stack_up: agent {AGENT_URL}/health not reachable after {STACK_WAIT_S:.0f}s")
        return 1
    print(f"[ok  ] stack_up: agent at {AGENT_URL} is healthy")

    runs: dict[str, dict] = {}
    all_clean = True

    # -- 1. /health contract ------------------------------------------------
    status, body, _ = http_get(f"{AGENT_URL}/health")
    ok = status == 200 and json.loads(body) == {"status": "ok"}
    check("health_contract", ok, f"GET /health -> {status} {body.strip()}")

    # -- 2. SPA serves the root mount --------------------------------------
    status, html, _ = http_get(WEB_URL)
    ok = status == 200 and 'id="root"' in html
    check("spa_serving", ok, f"GET {WEB_URL} -> {status}, root mount {'found' if ok else 'missing'}")

    # -- 3. CORS echoes the SPA origin -------------------------------------
    request = urllib.request.Request(
        f"{AGENT_URL}/copilotkit", method="OPTIONS",
        headers={"Origin": WEB_URL, "Access-Control-Request-Method": "POST"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as resp:
            allow = resp.headers.get("access-control-allow-origin")
    except urllib.error.HTTPError as exc:
        allow = exc.headers.get("access-control-allow-origin") if exc.headers else None
    check("cors_spa_origin", allow == WEB_URL,
          f"preflight Access-Control-Allow-Origin = {allow!r}")

    # -- 4-7. Q3 path -------------------------------------------------------
    runs["q3"] = run_query_sse(Q3_QUERY)
    kpi_item = item_by_type(runs["q3"], "kpi_row")
    kpis = ((kpi_item or {}).get("props") or {}).get("kpis") or []
    positive = [k for k in kpis if isinstance(k.get("delta"), (int, float)) and k["delta"] > 0]
    check("q3_kpi_row", bool(kpis) and len(positive) >= 3,
          f"{len(kpis)} KPIs, {len(positive)} positive-delta (need ≥3 of the row)")

    chart = item_by_type(runs["q3"], "line_chart") or {}
    props = chart.get("props") or {}
    points = props.get("points") or []
    check("q3_line_chart_range",
          props.get("range") == "2026-07-01..2026-09-30"
          and len(points) == 92
          and (not points or (points[0].get("day") == "2026-07-01" and points[-1].get("day") == "2026-09-30")),
          f"range={props.get('range')!r}, {len(points)} points (Jul 1 → Sep 30, 92 days)")

    ok = bool(runs["q3"].get("reply", "").strip())
    check("q3_reply", ok, f"assistant reply non-empty ({len(runs['q3'].get('reply', ''))} chars)")

    ok, detail = decisions_are_observable(runs["q3"])
    check("q3_decisions", ok, detail)

    # -- 8-10. churn path ----------------------------------------------------
    runs["churn"] = run_query_sse(CHURN_QUERY)
    callout = (item_by_type(runs["churn"], "variance_callout") or {}).get("props") or {}
    ok = (isinstance(callout.get("delta"), (int, float)) and callout["delta"] > 0.2
          and "pricing change" in str(callout.get("cause", "")).lower())
    check("churn_callout", ok,
          f"delta={callout.get('delta')}, cause={callout.get('cause')!r}")

    table = (item_by_type(runs["churn"], "data_table") or {}).get("props") or {}
    columns, rows = table.get("columns") or [], table.get("rows") or []
    needed = {"account", "segment", "mrr", "tenure_months", "plan"}
    ok = (38 <= len(rows) <= 50 and needed.issubset(set(columns))
          and all(str(r.get("segment")) == "Growth" for r in rows))
    check("churn_cohort_table", ok,
          f"{len(rows)} rows (gate 38–50), columns {columns}, all Growth")

    ok, detail = decisions_are_observable(runs["churn"])
    check("churn_decisions", ok, detail)

    # -- 11-13. DQ path --------------------------------------------------------
    runs["dq"] = run_query_sse(DQ_QUERY)
    card = (item_by_type(runs["dq"], "data_quality_card") or {}).get("props") or {}
    checks = {c.get("name"): c.get("status") for c in card.get("checks") or []}
    ok = checks == {"freshness": "ok", "duplicates": "fail", "nulls": "fail"}
    check("dq_checks", ok, f"checks = {checks} (gate: duplicates fail, nulls fail, freshness ok)")

    noul = decision_by_id(runs["dq"], "real_vs_data_problem") or {}
    p = (noul.get("probabilities") or {}).get("p")
    ok = isinstance(p, (int, float)) and p > 0.7 and noul.get("pick") == "data_problem"
    check("dq_noul_p_over_0_7", ok,
          f"noul p={p}, pick={noul.get('pick')!r} (gate p > 0.7)")

    warnings = (runs["dq"].get("status") or {}).get("warnings") or []
    ok = bool(warnings)
    check("dq_warning_banner", ok, f"status.warnings = {warnings}")

    # -- 14. decisions observable on the DQ run too (≥4 per query) ------------
    ok, detail = decisions_are_observable(runs["dq"])
    check("dq_decisions", ok, detail)

    # -- 15. off-catalog fallback ---------------------------------------------
    runs["off"] = run_query_sse(OFF_CATALOG_QUERY)
    types_here = {item.get("type") for item in items_of(runs["off"])}
    status_obj = runs["off"].get("status") or {}
    choice = decision_by_id(runs["off"], "component_choice") or {}
    sub_threshold = (
        choice.get("passed") is False
        and all(isinstance(p, (int, float)) and p < 0.6 for p in (choice.get("probabilities") or {}).values())
    )
    ok = (types_here == {"insight_text"}
          and status_obj.get("fallback") is True
          and sub_threshold)
    check("off_catalog_fallback", ok,
          f"items={sorted(types_here)}, status.fallback={status_obj.get('fallback')}, "
          f"component_choice passed={choice.get('passed')}, max p="
          f"{max((choice.get('probabilities') or {}).values(), default=0):.2f}")

    # -- 16. unknown markup never renders outside the catalog ------------------
    all_items = [item for run in runs.values() for item in items_of(run)]
    stray = {item.get("type") for item in all_items} - CATALOG_TYPES
    check("catalog_closed_world", not stray,
          f"every rendered item across {len(all_items)} items is a catalog member"
          + (f" (stray: {sorted(stray)})" if stray else ""))

    # -- 17. every run ends cleanly --------------------------------------------
    for label, run in runs.items():
        clean, detail = run_finished_cleanly(run, label)
        if not clean:
            all_clean = False
    check("runs_finished_clean", all_clean,
          f"{len(runs)} runs ended with RUN_FINISHED and zero RUN_ERROR")

    # -- summary ----------------------------------------------------------------
    print()
    print(f"{len(failures)} of the checks failed" + (f": {sorted(set(failures))}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

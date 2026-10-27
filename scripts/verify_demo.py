#!/usr/bin/env python3
"""End-to-end probe suite for the PRD section 11 checklist (issue #22).

Runs against the live stack (`docker compose up`): the FastAPI agent on
AGENT_URL (default http://localhost:8123) and the Vite SPA on WEB_URL
(default http://localhost:5173). No third-party imports — stdlib only
(urllib/json/uuid), so it runs on bare `python3` with no venv.

Wire protocol: POST {AGENT_URL}/copilotkit with the RunAgentInput shape
(camelCase aliases, exactly what the React HttpAgent sends) and
Accept: text/event-stream; the endpoint streams AG-UI SSE `data:` lines.
STATE_SNAPSHOT events carry the shared state keys (a2ui, decisions,
status, snapshot, messages) — tracked independently, last-wins-on-present,
exactly like the browser's useA2UI/useDecisions hooks. Every query runs
on a fresh threadId so runs never share state.

Readiness: /health answers instantly at boot while the demo database is
still seeding in the background (server.py self-seeds data/app.db); a
run during the seeding window degrades instead of failing. So after the
ports answer, the script re-runs the Q3 query until its snapshot reads
degraded=False before the counted checks start.

Exit code: 0 when every check passes, 1 otherwise. Output is a per-check
[ok]/[FAIL] report plus a summary line.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8123").rstrip("/")
WEB_URL = os.getenv("WEB_URL", "http://localhost:5173").rstrip("/")

Q3_QUERY = "Show me sales in Q3"
Q3_RANGE = "2026-07-01..2026-09-30"
CHURN_QUERY = "Why did churn spike in August?"
DQ_QUERY = "Is the data clean?"
OFF_CATALOG_QUERY = "draw me a dragon"

STACK_TIMEOUT_S = float(os.getenv("VERIFY_STACK_TIMEOUT_S", "300"))
QUERY_TIMEOUT_S = float(os.getenv("VERIFY_QUERY_TIMEOUT_S", "180"))
READINESS_ATTEMPTS = int(os.getenv("VERIFY_READINESS_ATTEMPTS", "6"))

CHECKS = []


def check(name):
    """Register a probe: the decorated function returns (passed, detail)."""

    def wrap(fn):
        CHECKS.append((name, fn))
        return fn

    return wrap


def http_get(url, timeout=10):
    """GET url -> (status, body text). Never raises: errors read as (0, str)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - every failure mode is a probe miss
        return 0, str(exc)


def wait_for_stack():
    """Block until /health answers the exact contract and the SPA serves,
    or return an error string after STACK_TIMEOUT_S."""
    deadline = time.monotonic() + STACK_TIMEOUT_S
    agent_ok = web_ok = False
    while time.monotonic() < deadline:
        if not agent_ok:
            status, body = http_get(f"{AGENT_URL}/health")
            agent_ok = status == 200 and json.loads(body or "null") == {
                "status": "ok"
            }
        if not web_ok:
            status, _ = http_get(f"{WEB_URL}/")
            web_ok = status == 200
        if agent_ok and web_ok:
            return None
        time.sleep(2)
    missing = []
    if not agent_ok:
        missing.append(f"agent {AGENT_URL}/health")
    if not web_ok:
        missing.append(f"web {WEB_URL}/")
    return "stack not ready within %ss: %s" % (STACK_TIMEOUT_S, ", ".join(missing))


def run_query_sse(query, timeout=QUERY_TIMEOUT_S):
    """One full graph run over the real AG-UI HTTP endpoint.

    Returns a dict with: raw (full SSE text), snapshots (parsed
    STATE_SNAPSHOT state dicts in order), finished (RUN_FINISHED seen),
    errored (RUN_ERROR seen), plus last-wins a2ui/decisions/status/messages.
    Raises RuntimeError on transport failure or an empty snapshot stream.
    """
    body = json.dumps(
        {
            "threadId": f"verify-{uuid.uuid4()}",
            "runId": f"run-{uuid.uuid4()}",
            "state": {},
            "messages": [{"id": "m1", "role": "user", "content": query}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{AGENT_URL}/copilotkit",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"POST /copilotkit -> HTTP {exc.code}: {exc.read().decode()[:300]}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as a probe failure
        raise RuntimeError(f"POST /copilotkit failed: {exc}") from exc

    deadline = time.monotonic() + timeout
    raw_lines = []
    snapshots = []
    finished = errored = False
    with response:
        while time.monotonic() < deadline:
            line = response.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip("\n")
            raw_lines.append(text)
            if "RUN_FINISHED" in text:
                finished = True
            if "RUN_ERROR" in text:
                errored = True
            if not text.startswith("data:"):
                continue
            payload = text[len("data:"):].strip()
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if "STATE_SNAPSHOT" in text or event.get("type") == "STATE_SNAPSHOT":
                snapshot = event.get("snapshot", event.get("state"))
                if isinstance(snapshot, dict):
                    snapshots.append(snapshot)
    if not snapshots:
        raise RuntimeError(
            "no STATE_SNAPSHOT events streamed (raw tail: %s)"
            % "\n".join(raw_lines[-5:])
        )
    merged = {}
    for snapshot in snapshots:
        for key in ("a2ui", "decisions", "status", "snapshot", "messages"):
            if snapshot.get(key) is not None:
                merged[key] = snapshot[key]
    return {
        "raw": "\n".join(raw_lines),
        "snapshots": snapshots,
        "finished": finished,
        "errored": errored,
        "a2ui": merged.get("a2ui"),
        "decisions": merged.get("decisions"),
        "status": merged.get("status"),
        "snapshot": merged.get("snapshot"),
        "messages": merged.get("messages"),
    }


def wait_for_seed():
    """Re-run the Q3 query until its snapshot is seeded (degraded=False)
    and carries a kpi_row, or raise RuntimeError after READINESS_ATTEMPTS."""
    last = None
    for attempt in range(1, READINESS_ATTEMPTS + 1):
        try:
            result = run_query_sse(Q3_QUERY)
        except RuntimeError as exc:
            last = str(exc)
            time.sleep(10)
            continue
        snapshot = result.get("snapshot") or {}
        items = (result.get("a2ui") or {}).get("items") or []
        types = {item.get("type") for item in items if isinstance(item, dict)}
        if snapshot.get("degraded") is False and "kpi_row" in types:
            return result
        last = "attempt %d: degraded=%r types=%s" % (
            attempt,
            snapshot.get("degraded"),
            sorted(types),
        )
        time.sleep(10)
    raise RuntimeError("database never finished seeding: %s" % last)


def items_by_type(a2ui):
    """type -> [props] for a validated a2ui layout dict (tolerates junk)."""
    items = (a2ui or {}).get("items") if isinstance(a2ui, dict) else None
    grouped = {}
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("type"), str):
            grouped.setdefault(item["type"], []).append(item.get("props") or {})
    return grouped


def decisions_shape_ok(decisions):
    """Every decision carries a non-empty model and a numeric latency."""
    if not isinstance(decisions, list) or len(decisions) < 4:
        return False, "expected >=4 decisions, got %r" % (
            len(decisions) if isinstance(decisions, list) else type(decisions)
        )
    for decision in decisions:
        model = decision.get("model")
        latency = decision.get("latency_ms")
        if not isinstance(model, str) or not model:
            return False, "decision %r has no model" % decision.get("id")
        if (
            not isinstance(latency, (int, float))
            or isinstance(latency, bool)
            or latency < 0
        ):
            return False, "decision %r has bad latency_ms=%r" % (
                decision.get("id"),
                latency,
            )
    models = sorted({d["model"] for d in decisions})
    return True, "%d decisions, models=%s" % (len(decisions), models)


def assistant_text(result):
    """The run's closing assistant text (final AI message, else summary)."""
    for message in reversed(result.get("messages") or []):
        if isinstance(message, dict) and message.get("type") == "ai":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    summary = ((result.get("a2ui") or {}).get("slotCopy") or {}).get("summary")
    return summary.strip() if isinstance(summary, str) else ""


RUNS = {}


@check("agent /health answers the exact contract")
def probe_health():
    status, body = http_get(f"{AGENT_URL}/health")
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    ok = status == 200 and parsed == {"status": "ok"}
    return ok, f"GET /health -> {status} {body[:60]!r}"


@check("SPA serves 200 with a root mount div")
def probe_web():
    status, body = http_get(f"{WEB_URL}/")
    ok = status == 200 and '<div id="root"' in (body or "")
    return ok, f"GET / -> {status} ({len(body or '')} bytes)"


@check("Q3 path streams kpi_row with 3-4 KPI tiles")
def probe_q3_kpis():
    grouped = items_by_type(RUNS["q3"]["a2ui"])
    kpi_rows = grouped.get("kpi_row") or []
    if not kpi_rows:
        return False, "no kpi_row in %s" % sorted(grouped)
    kpis = kpi_rows[0].get("kpis") or []
    ok = (
        3 <= len(kpis) <= 4
        and all(
            isinstance(k, dict)
            and isinstance(k.get("label"), str)
            and isinstance(k.get("value"), (int, float))
            and not isinstance(k.get("value"), bool)
            and isinstance(k.get("delta"), (int, float))
            for k in kpis
        )
    )
    labels = [k.get("label") for k in kpis if isinstance(k, dict)]
    return ok, "kpis=%s" % labels


@check("Q3 path streams line_chart over 2026-07-01..2026-09-30")
def probe_q3_chart():
    grouped = items_by_type(RUNS["q3"]["a2ui"])
    charts = grouped.get("line_chart") or []
    if not charts:
        return False, "no line_chart in %s" % sorted(grouped)
    ok = charts[0].get("range") == Q3_RANGE
    return ok, "range=%r" % charts[0].get("range")


@check("Q3 path ends with a non-empty assistant reply")
def probe_q3_reply():
    result = RUNS["q3"]
    text = assistant_text(result)
    ok = bool(text) and result["finished"] and not result["errored"]
    return ok, "reply=%r finished=%s" % (text[:80], result["finished"])


@check("churn path streams variance_callout with pricing cause")
def probe_churn_callout():
    grouped = items_by_type(RUNS["churn"]["a2ui"])
    callouts = grouped.get("variance_callout") or []
    if not callouts:
        return False, "no variance_callout in %s" % sorted(grouped)
    props = callouts[0]
    delta = props.get("delta")
    cause = props.get("cause") or ""
    ok = (
        isinstance(delta, (int, float))
        and delta > 0.2
        and "pricing" in cause.lower()
    )
    return ok, "delta=%r cause=%r" % (delta, cause[:80])


@check("churn path streams data_table with 38-50 cohort rows")
def probe_churn_table():
    grouped = items_by_type(RUNS["churn"]["a2ui"])
    tables = grouped.get("data_table") or []
    if not tables:
        return False, "no data_table in %s" % sorted(grouped)
    props = tables[0]
    rows = props.get("rows") or []
    columns = set(props.get("columns") or [])
    ok = (
        38 <= len(rows) <= 50
        and {"segment", "mrr", "tenure_months", "plan"} <= columns
    )
    return ok, "%d rows columns=%s" % (len(rows), sorted(columns))


@check("DQ path surfaces duplicates:fail, nulls:fail, freshness:ok")
def probe_dq_checks():
    grouped = items_by_type(RUNS["dq"]["a2ui"])
    cards = grouped.get("data_quality_card") or []
    if not cards:
        return False, "no data_quality_card in %s" % sorted(grouped)
    got = {
        row.get("name"): row.get("status")
        for row in (cards[0].get("checks") or [])
        if isinstance(row, dict)
    }
    want = {"duplicates": "fail", "nulls": "fail", "freshness": "ok"}
    ok = all(got.get(name) == status for name, status in want.items())
    return ok, "checks=%s" % got


@check("DQ path records data_problem noul with p > 0.7")
def probe_dq_noul():
    matches = [
        d
        for d in (RUNS["dq"]["decisions"] or [])
        if d.get("id") == "data_problem"
    ]
    if not matches:
        return False, "no data_problem decision in %d decisions" % len(
            RUNS["dq"]["decisions"] or []
        )
    probabilities = matches[0].get("probabilities")
    if isinstance(probabilities, dict):
        value = max(probabilities.values())
    else:
        value = probabilities
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0.7
    return ok, "data_problem p=%r" % value


@check("Q3 decisions: >=4 entries with model + latency")
def probe_q3_decisions():
    return decisions_shape_ok(RUNS["q3"]["decisions"])


@check("churn decisions: >=4 entries with model + latency")
def probe_churn_decisions():
    return decisions_shape_ok(RUNS["churn"]["decisions"])


@check("DQ decisions: >=4 entries with model + latency")
def probe_dq_decisions():
    return decisions_shape_ok(RUNS["dq"]["decisions"])


@check("off-catalog query renders only the insight_text fallback")
def probe_off_catalog_layout():
    a2ui = RUNS["off_catalog"]["a2ui"] or {}
    types = [
        item.get("type") for item in a2ui.get("items") or [] if isinstance(item, dict)
    ]
    ok = bool(types) and set(types) == {"insight_text"}
    return ok, "types=%s" % types


@check("off-catalog query records a sub-threshold decision")
def probe_off_catalog_decision():
    decisions = RUNS["off_catalog"]["decisions"] or []
    if not decisions:
        return False, "no decisions recorded"
    ok = all(d.get("above_threshold") is False for d in decisions)
    return ok, "%d decisions, all above_threshold=False" % len(decisions)


@check("every run ends RUN_FINISHED with no RUN_ERROR")
def probe_runs_finished():
    bad = [
        name
        for name, result in RUNS.items()
        if not result["finished"] or result["errored"]
    ]
    ok = not bad
    return ok, "runs=%s" % (
        "all finished clean" if ok else "bad=%s" % bad
    )


def main():
    failed_before = wait_for_stack()
    if failed_before:
        print(f"[FAIL] stack ready: {failed_before}")
        print("0/1 checks passed")
        return 1
    print("[ok] stack ready: agent + web answer")

    try:
        RUNS["q3"] = wait_for_seed()
    except RuntimeError as exc:
        print(f"[FAIL] seed readiness: {exc}")
        print("0/1 checks passed")
        return 1
    print("[ok] seed readiness: Q3 run is seeded (kpi_row streamed)")

    for name, query in (
        ("churn", CHURN_QUERY),
        ("dq", DQ_QUERY),
        ("off_catalog", OFF_CATALOG_QUERY),
    ):
        try:
            RUNS[name] = run_query_sse(query)
        except RuntimeError as exc:
            print(f"[FAIL] run {name} ({query!r}): {exc}")
            print("0/1 checks passed")
            return 1
        print(f"[ok] run {name}: streamed "
              f"{len(RUNS[name]['snapshots'])} snapshots")

    passed = 0
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a raising probe is a failed probe
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")
        passed += ok
    print(f"{passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Release slice verifier: upload -> triple-query parity -> Postgres parity
-> off-catalog safety in one executable (issue #91).

Runs against the live stack (`docker compose up -d`): the FastAPI agent on
AGENT_URL (default http://localhost:8123) and the Vite SPA on WEB_URL
(default http://localhost:5173). No third-party imports — stdlib only
(urllib/json/uuid/csv/argparse), so it runs on bare `python3` with no venv,
mirroring the `scripts/verify_demo.py` probe idiom.

Slice: the reviewer-scale `samples/*.csv` miniature (issue #72) loaded via
POST /data/sample (`--target sqlite`, the default, proves the empty-boot
path: a clean-clone boot holds no dataset and the verifier loads one
itself) or the same CSVs pre-loaded into Postgres by
`scripts/load_postgres.sh` (`--target postgres`, which requires
DATASOURCE_URL to already select that database).

Triple-query parity over the August spike window (2026-08-01..2026-08-31,
holding all 20 sample cancels): the NL churn total (sum of `mrr` over the
`/copilotkit` data_table rows for "Why did churn spike in August?"), the
picker total (sum of `mrr` over the `GET /data/run` data_table rows for
metric=gross_churned_arr) and the SQL spot-check total
(`POST /data/query` SELECT of per-row `mrr`) must agree within 1% of each
other and of the CSV ground truth (sum of churn-time ARR over the same
window, computed straight from the CSVs). The success line is exactly
`parity: ok`.

Off-catalog safety: `draw me a dragon` via `/copilotkit` must render only
`insight_text` items. Upload isolation: `POST /data/upload` (valid and
rejected shapes) must leave the active dataset pointer untouched.

Wire protocol: POST {AGENT_URL}/copilotkit with the RunAgentInput shape
(camelCase aliases, exactly what the React HttpAgent sends) and
Accept: text/event-stream; the endpoint streams AG-UI SSE `data:` lines.
STATE_SNAPSHOT events carry the shared state keys (a2ui, decisions,
status, snapshot, messages) — tracked independently, last-wins-on-present,
exactly like the browser's useA2UI/useDecisions hooks. Every query runs
on a fresh threadId so runs never share state.

Exit code: 0 when every check passes (and `parity: ok` is printed), 1
otherwise. Output is a per-check [ok]/[FAIL] report plus a summary line.

Usage:
  python scripts/verify_data_slice.py [--target sqlite|postgres]
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8123").rstrip("/")
WEB_URL = os.getenv("WEB_URL", "http://localhost:5173").rstrip("/")

# The August spike window: holds all 20 sample cancels (samples/README.md
# inventory: 20 `cancel` rows inside 2026-08-11..2026-08-28).
SLICE_START = "2026-08-01"
SLICE_END = "2026-08-31"
SLICE_METRIC = "gross_churned_arr"
CHURN_QUERY = "Why did churn spike in August?"
OFF_CATALOG_QUERY = "draw me a dragon"

# Repo-root samples/ anchored from this file's location, never the cwd
# (agent/db.py precedent) — the CSV ground truth the parity legs prove.
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"

STACK_TIMEOUT_S = float(os.getenv("VERIFY_STACK_TIMEOUT_S", "300"))
QUERY_TIMEOUT_S = float(os.getenv("VERIFY_QUERY_TIMEOUT_S", "180"))

# Parity tolerance: 1% relative; a zero base falls back to an absolute
# 1e-6 so an empty-window comparison stays exact instead of dividing.
PARITY_PCT = 1.0
PARITY_ABS = 1e-6

CHECKS = []


def check(name):
    """Register a probe: the decorated function returns (passed, detail)."""

    def wrap(fn):
        CHECKS.append((name, fn))
        return fn

    return wrap


def within_pct(first, second, pct=PARITY_PCT):
    """True when two totals agree within `pct` percent (relative to the
    second); a zero base compares absolutely within PARITY_ABS."""
    base = abs(second)
    if base == 0:
        return abs(first - second) <= PARITY_ABS
    return abs(first - second) <= base * pct / 100.0


def mrr_total(rows):
    """Sum of the `mrr` column over data_table/SQL rows (floats; junk rows
    read 0.0 so a malformed cell fails parity instead of raising)."""
    total = 0.0
    for row in rows or []:
        if isinstance(row, dict):
            value = row.get("mrr")
        elif isinstance(row, (list, tuple)) and row:
            value = row[0]
        else:
            value = None
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            total += float(value)
    return total


def http_get(url, timeout=10):
    """GET url -> (status, body text). Never raises: errors read as (0, str)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - every failure mode is a probe miss
        return 0, str(exc)


def http_post_json(url, payload, timeout=30):
    """POST a JSON body -> (status, parsed JSON or raw text). Never raises:
    transport failures read as (0, str)."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - a non-JSON error body stays text
            return exc.code, ""
    except Exception as exc:  # noqa: BLE001 - every failure mode is a probe miss
        return 0, str(exc)


def http_post_multipart(url, fields, files, timeout=30):
    """POST multipart fields/files -> (status, parsed JSON or raw text).

    `fields` maps name -> str value; `files` maps name ->
    (filename, bytes, content-type). Boundary framing is literal (no third
    party), matching what FastAPI's UploadFile/Form pair parses.
    """
    boundary = f"slice-{uuid.uuid4().hex}"
    body = b""
    for name, value in fields.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n{value}\r\n"
        ).encode("utf-8")
    for name, (filename, content, ctype) in files.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'
        ).encode("utf-8") + content + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - a non-JSON error body stays text
            return exc.code, ""
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
    errored (RUN_ERROR seen), plus last-wins a2ui/decisions/status/snapshot.
    Raises RuntimeError on transport failure or an empty snapshot stream.
    """
    body = json.dumps(
        {
            "threadId": f"slice-{uuid.uuid4()}",
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
    }


def items_by_type(a2ui):
    """type -> [props] for a validated a2ui layout dict (tolerates junk)."""
    items = (a2ui or {}).get("items") if isinstance(a2ui, dict) else None
    grouped = {}
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("type"), str):
            grouped.setdefault(item["type"], []).append(item.get("props") or {})
    return grouped


def csv_ground_truth(start=SLICE_START, end=SLICE_END):
    """The parity anchor straight from the CSVs: (mrr dollars, cancels,
    data_end) where mrr sums churn-time ARR over `cancel` events in
    [start, end] divided by 12 — the per-row `mrr` definition the NL,
    picker and SQL legs all sum (`ROUND(arr_cents / 1200.0, 2)`).
    data_end is the union-MAX event_date over the three event CSVs."""
    accounts = {}
    with open(SAMPLES_DIR / "accounts.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            accounts[row["id"]] = int(row["arr_cents"])
    cancels = 0
    cents = 0
    with open(
        SAMPLES_DIR / "subscription_events.csv", newline="", encoding="utf-8"
    ) as handle:
        for row in csv.DictReader(handle):
            if row["event_type"] == "cancel" and start <= row["event_date"] <= end:
                cancels += 1
                cents += accounts.get(row["account_id"], 0)
    data_end = None
    for name in ("subscription_events", "revenue_events", "usage_events"):
        with open(SAMPLES_DIR / f"{name}.csv", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                day = row["event_date"]
                if data_end is None or day > data_end:
                    data_end = day
    return cents / 1200.0, cancels, data_end


STATE = {}


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


@check("sample miniature loads and activates (upload-first slice)")
def probe_sample():
    sample = STATE.get("sample")
    if not sample:
        return False, "POST /data/sample failed before checks ran"
    ok, detail = sample["ok"], sample["detail"]
    return ok, detail


@check("postgres source activates for query legs")
def probe_activate():
    if STATE["target"] != "postgres":
        return True, "target=sqlite: postgres activation skipped"
    entry = STATE.get("activated")
    if not entry:
        return False, "POST /data/active postgres failed before checks ran"
    return entry["ok"], entry["detail"]


@check("upload spot-check leaves the active dataset untouched (isolation)")
def probe_upload_isolation():
    upload = STATE.get("upload")
    if not upload:
        return False, "POST /data/upload probe failed before checks ran"
    return upload["ok"], upload["detail"]


@check("picker leg: GET /data/run streams the August churn cohort")
def probe_picker():
    picker = STATE.get("picker")
    if not picker:
        return False, "GET /data/run failed before checks ran"
    return picker["ok"], picker["detail"]


@check("SQL leg: POST /data/query spot-check sums the same cohort")
def probe_sql():
    sql = STATE.get("sql")
    if not sql:
        return False, "POST /data/query failed before checks ran"
    return sql["ok"], sql["detail"]


@check("NL leg: churn question streams a data_table cohort")
def probe_nl():
    nl = STATE.get("nl")
    if not nl:
        return False, "churn /copilotkit run failed before checks ran"
    return nl["ok"], nl["detail"]


@check("triple-query totals agree within 1% of CSV ground truth")
def probe_parity():
    parts = []
    for leg in ("nl", "picker", "sql"):
        entry = STATE.get(leg)
        if not entry or not entry.get("ok"):
            return False, f"{leg} leg missing, parity unprovable"
        parts.append((leg, entry["total"]))
    truth = STATE.get("truth")
    if truth is None:
        return False, "CSV ground truth missing, parity unprovable"
    totals = [total for _, total in parts] + [truth]
    base = max(abs(value) for value in totals) or 1.0
    spread = max(totals) - min(totals)
    ok = all(
        within_pct(first, second)
        for first in totals
        for second in totals
    )
    detail = "%s vs truth %.2f (spread %.4f on base %.2f)" % (
        ", ".join(f"{leg}={total:.2f}" for leg, total in parts),
        truth,
        spread,
        base,
    )
    return ok, detail


@check("postgres leg: loader-backed source serves the same slice")
def probe_postgres():
    if STATE["target"] != "postgres":
        return True, "target=sqlite: postgres leg skipped"
    entry = STATE.get("postgres")
    if not entry:
        return False, "postgres probe failed before checks ran"
    return entry["ok"], entry["detail"]


@check("off-catalog query renders only the insight_text fallback")
def probe_off_catalog():
    off = STATE.get("off_catalog")
    if not off:
        return False, "off-catalog /copilotkit run failed before checks ran"
    return off["ok"], off["detail"]


def _load_sample_slice():
    """POST /data/sample and record the shape contract for probe_sample."""
    status, payload = http_post_json(f"{AGENT_URL}/data/sample", {})
    if status != 200 or not isinstance(payload, dict):
        return {"ok": False, "detail": f"POST /data/sample -> {status} {payload!r}"[:200]}
    shape_ok = (
        set(payload) == {"active_id", "tables", "rows", "data_end"}
        and payload["tables"]
        == ["accounts", "revenue_events", "subscription_events", "usage_events"]
        and isinstance(payload["rows"], int)
        and payload["rows"] > 0
    )
    _, _, data_end = csv_ground_truth()
    end_ok = payload.get("data_end") == data_end
    ok = shape_ok and end_ok
    STATE["active_id"] = payload.get("active_id")
    return {
        "ok": ok,
        "active_id": payload.get("active_id"),
        "detail": "active_id=%s tables=%s rows=%s data_end=%s (csv %s)"
        % (
            str(payload.get("active_id"))[:8],
            payload.get("tables"),
            payload.get("rows"),
            payload.get("data_end"),
            data_end,
        ),
    }


def _activate(dataset_id):
    """POST /data/active and record the new pointer; returns (ok, detail)."""
    status, payload = http_post_json(f"{AGENT_URL}/data/active", {"id": dataset_id})
    ok = status == 200 and isinstance(payload, dict) and payload.get("active_id") == dataset_id
    if ok:
        STATE["active_id"] = dataset_id
    return ok, f"POST /data/active {dataset_id} -> {status} active={STATE.get('active_id')}"


def _upload_isolation_slice():
    """One valid + one rejected upload; both must leave activation alone."""
    before = STATE.get("active_id")
    if before is None:
        status, listing = http_get(f"{AGENT_URL}/data/datasets")
        try:
            before = (json.loads(listing) or {}).get("active_id")
        except json.JSONDecodeError:
            before = None
        STATE["active_id"] = before
    raw = (SAMPLES_DIR / "accounts.csv").read_bytes()
    status, payload = http_post_multipart(
        f"{AGENT_URL}/data/upload",
        {"table": "accounts"},
        {"file": ("accounts.csv", raw, "text/csv")},
    )
    valid_ok = (
        status == 201
        and isinstance(payload, dict)
        and payload.get("table") == "accounts"
        and isinstance(payload.get("rows"), int)
        and payload["rows"] > 0
    )
    status_bad, payload_bad = http_post_multipart(
        f"{AGENT_URL}/data/upload",
        {"table": "bogus"},
        {"file": ("accounts.csv", raw, "text/csv")},
    )
    rejected_ok = status_bad == 400 and (
        isinstance(payload_bad, dict) and "error" in payload_bad
    )
    _, listing = http_get(f"{AGENT_URL}/data/datasets")
    try:
        active_after = (json.loads(listing) or {}).get("active_id")
    except json.JSONDecodeError:
        active_after = "<unparsable>"
    # Isolation means the pointer did not move — including None -> None on
    # a stack where nothing was active yet.
    still_active = active_after == before
    ok = valid_ok and rejected_ok and still_active
    return {
        "ok": ok,
        "detail": "upload -> %s rows=%s, bogus -> %s, active %s still %s"
        % (
            status,
            (payload.get("rows") if isinstance(payload, dict) else None),
            status_bad,
            active_after,
            "pinned" if still_active else f"MOVED (was {before})",
        ),
    }


def _picker_slice():
    """GET /data/run for the slice metric; record the cohort mrr total."""
    params = urllib.parse.urlencode(
        {
            "metric": SLICE_METRIC,
            "start": SLICE_START,
            "end": SLICE_END,
            "segment": "All",
        }
    )
    status, body = http_get(f"{AGENT_URL}/data/run?{params}")
    try:
        payload = json.loads(body) if body else None
    except json.JSONDecodeError:
        payload = None
    if status != 200 or not isinstance(payload, dict):
        return {"ok": False, "detail": f"GET /data/run -> {status} {(body or '')[:160]!r}"}
    tables = items_by_type(payload).get("data_table") or []
    if not tables:
        return {
            "ok": False,
            "detail": "GET /data/run 200 but no data_table in %s"
            % sorted(items_by_type(payload)),
        }
    total = mrr_total(tables[0].get("rows"))
    rows = len(tables[0].get("rows") or [])
    ok = rows > 0
    return {
        "ok": ok,
        "total": total,
        "detail": f"picker cohort {rows} rows total={total:.2f}",
    }


def _sql_slice():
    """POST /data/query SELECT of per-row mrr over the slice window.

    The guarded SQL hatch is SQLite-only by design (issue #83): on
    --target postgres the active world is switched to the sqlite sample
    for this one call and restored to postgres afterwards, so the SQL
    total proves the sqlite sandbox while the picker/NL totals prove
    Postgres — genuine cross-backend parity in a single run."""
    backend_note = "sqlite sandbox"
    if STATE["target"] == "postgres":
        sample_id = (STATE.get("sample") or {}).get("active_id")
        if not sample_id:
            return {"ok": False, "detail": "no sample id to switch to"}
        ok_switch, detail_switch = _activate(sample_id)
        if not ok_switch:
            return {"ok": False, "detail": f"switch to sample failed: {detail_switch}"}
        backend_note = "sqlite sandbox (postgres legs stay on Postgres)"
    try:
        sql = (
            "SELECT ROUND(a.arr_cents / 1200.0, 2) AS mrr "
            "FROM subscription_events s JOIN accounts a ON a.id = s.account_id "
            f"WHERE s.event_type = 'cancel' AND s.event_date BETWEEN '{SLICE_START}' "
            f"AND '{SLICE_END}' ORDER BY s.event_date, s.account_id"
        )
        status, payload = http_post_json(f"{AGENT_URL}/data/query", {"sql": sql})
    finally:
        if STATE["target"] == "postgres":
            _activate("postgres")
    if status != 200 or not isinstance(payload, dict):
        return {
            "ok": False,
            "detail": f"POST /data/query -> {status} {str(payload)[:160]!r}",
        }
    if not isinstance(payload.get("columns"), list) or "mrr" not in payload["columns"]:
        return {"ok": False, "detail": f"unexpected shape {str(payload)[:160]!r}"}
    rows = payload.get("rows") or []
    total = mrr_total([row if isinstance(row, list) else [] for row in rows])
    # The SELECT projects exactly one column, so each row's first cell is mrr.
    ok = len(rows) > 0
    return {
        "ok": ok,
        "total": total,
        "detail": "sql cohort %d rows total=%.2f (%s)"
        % (len(rows), total, backend_note),
    }


def _postgres_slice():
    """On --target postgres: the loader-backed source must be listed with
    the sample row counts — proving the server's DATASOURCE_URL selects the
    load_postgres.sh-loaded DB the query legs then read."""
    status, body = http_get(f"{AGENT_URL}/data/datasets")
    try:
        listing = json.loads(body) if body else None
    except json.JSONDecodeError:
        listing = None
    if status != 200 or not isinstance(listing, dict):
        return {"ok": False, "detail": f"GET /data/datasets -> {status}"}
    entries = listing.get("datasets") or []
    pg = [entry for entry in entries if entry.get("id") == "postgres"]
    if not pg:
        return {
            "ok": False,
            "detail": "no postgres entry in %s (is DATASOURCE_URL set?)"
            % [entry.get("id") for entry in entries if isinstance(entry, dict)],
        }
    ok = pg[0].get("source") == "postgres" and (pg[0].get("rows") or 0) > 0
    return {
        "ok": ok,
        "detail": "postgres source=%s rows=%s data_end=%s"
        % (pg[0].get("source"), pg[0].get("rows"), pg[0].get("data_end")),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="issue #91 slice verifier: upload -> triple-query "
        "parity -> postgres parity -> off-catalog safety"
    )
    parser.add_argument(
        "--target",
        choices=("sqlite", "postgres"),
        default="sqlite",
        help="backend under test: sqlite loads the samples miniature, "
        "postgres assumes DATASOURCE_URL already points at a "
        "load_postgres.sh-loaded DB (default: sqlite)",
    )
    args = parser.parse_args(argv)
    STATE["target"] = args.target
    STATE["truth"], STATE["expected_cancels"], _ = csv_ground_truth()

    failed_before = wait_for_stack()
    if failed_before:
        print(f"[FAIL] stack ready: {failed_before}")
        print("0/1 checks passed")
        return 1
    print("[ok] stack ready: agent + web answer")

    if args.target == "postgres":
        # The loader-backed source proves itself: the server must list the
        # postgres entry (which only appears when its own DATASOURCE_URL
        # selects it) — no client-side env read, the server is the source
        # of truth.
        STATE["postgres"] = _postgres_slice()
        print(
            "[%s] postgres source listed: %s"
            % ("ok" if STATE["postgres"]["ok"] else "FAIL", STATE["postgres"]["detail"])
        )
        if not STATE["postgres"]["ok"]:
            print("0/1 checks passed")
            return 1
    try:
        STATE["sample"] = _load_sample_slice()
    except OSError as exc:
        STATE["sample"] = {"ok": False, "detail": f"sample load failed: {exc}"}
    print(
        "[%s] sample miniature loads: %s"
        % ("ok" if STATE["sample"]["ok"] else "FAIL", STATE["sample"]["detail"])
    )
    if not STATE["sample"]["ok"]:
        print("0/1 checks passed")
        return 1
    if args.target == "postgres":
        # Query legs (read_state, NL, picker) scope to the registry's
        # active world, so the loader-backed source must be activated
        # before they can read it; the sqlite sample stays loaded for the
        # SQL hatch, which is SQLite-only by design (issue #83).
        ok_active, detail_active = _activate("postgres")
        STATE["activated"] = {"ok": ok_active, "detail": detail_active}
        print("[%s] activate postgres: %s" % ("ok" if ok_active else "FAIL", detail_active))
        if not ok_active:
            print("0/1 checks passed")
            return 1

    try:
        STATE["upload"] = _upload_isolation_slice()
    except OSError as exc:
        STATE["upload"] = {"ok": False, "detail": f"upload probe failed: {exc}"}
    print(
        "[%s] upload isolation: %s"
        % ("ok" if STATE["upload"]["ok"] else "FAIL", STATE["upload"]["detail"])
    )

    STATE["picker"] = _picker_slice()
    print(
        "[%s] picker leg: %s"
        % ("ok" if STATE["picker"]["ok"] else "FAIL", STATE["picker"]["detail"])
    )
    STATE["sql"] = _sql_slice()
    print(
        "[%s] sql leg: %s"
        % ("ok" if STATE["sql"]["ok"] else "FAIL", STATE["sql"]["detail"])
    )

    for name, query in (("nl", CHURN_QUERY), ("off_catalog", OFF_CATALOG_QUERY)):
        try:
            result = run_query_sse(query)
        except RuntimeError as exc:
            print(f"[FAIL] run {name} ({query!r}): {exc}")
            print("0/1 checks passed")
            return 1
        print(f"[ok] run {name}: streamed {len(result['snapshots'])} snapshots")
        if name == "nl":
            grouped = items_by_type(result["a2ui"])
            tables = grouped.get("data_table") or []
            if tables:
                total = mrr_total(tables[0].get("rows"))
                rows = len(tables[0].get("rows") or [])
                STATE["nl"] = {
                    "ok": rows > 0 and result["finished"] and not result["errored"],
                    "total": total,
                    "detail": "nl cohort %s rows total=%.2f finished=%s"
                    % (rows, total, result["finished"]),
                }
            else:
                STATE["nl"] = {
                    "ok": False,
                    "detail": "no data_table in %s" % sorted(grouped),
                }
        else:
            items = (result["a2ui"] or {}).get("items") or []
            types = [item.get("type") for item in items if isinstance(item, dict)]
            STATE["off_catalog"] = {
                "ok": bool(types) and set(types) == {"insight_text"},
                "detail": f"types={types}",
            }

    passed = 0
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a raising probe is a failed probe
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")
        passed += ok
    print(f"{passed}/{len(CHECKS)} checks passed")
    if passed == len(CHECKS):
        print("parity: ok")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

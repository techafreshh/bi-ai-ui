#!/usr/bin/env python3
"""Paraphrase generalization eval — report-only, never a merge gate (issue #89).

Runs ~20 paraphrases per golden path (sales / churn / quality) plus 10
distractors over a seeded-shape sandbox and `--seed 7/11/23` random-profile
sandboxes, under the heuristic decider (and the live decider when `--live`
or `LIVE=1` is set). Prints a markdown table, saves `eval/report.md`, and
exits 0 even when accuracy is low — the numbers inform owners, they never
block a merge.

Accuracy is the exact component-pair match of the streamed `a2ui` item
types against the expected route sets; unknown-type counts item types
outside `questions.CATALOG_TYPES` (always 0 — the layout node validates);
fallback rate counts `fallback_layout()`-only canvases. Median latency
times the decide-node wall clock per query.

Sandboxes are built in a temp dir through the `generate_data.py`
entrypoint as a subprocess (never committed); only `eval/report.md` is
committed. Each query drives `read_state -> decide -> layout` in-process
with the sandbox registered as the single-active dataset (the
`test_graph.py` seeded-DB idiom) — no server, no chat-model key needed.
Live mode with no `OPENROUTER_API_KEY` records `n/a (no key)` cells
instead of failing; exit stays 0.

Usage (from the repo root):
  python scripts/eval_paraphrase.py [--seed 7 --seed 11 --seed 23] [--live]

Exit code: 0 on every completed run (low accuracy never fails); nonzero
only on infrastructure errors (sandbox build or in-process run raising).
"""

import argparse
import asyncio
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO_ROOT / "agent"
GENERATOR = AGENT_DIR / "scripts" / "generate_data.py"
REPORT_PATH = REPO_ROOT / "eval" / "report.md"
GENERATOR_TIMEOUT_S = 600

# The sys.path bootstrap adds ONLY agent/ (not the repo root): the agent
# modules dual-import `db`/`datasets` (package-style `from agent import db`
# first), and repo-root visibility would flip every bind to `agent.db`
# while this script swaps the top-level registry — two distinct module
# objects would leave the swap seam silently dead. This is the
# `agent/tests/conftest.py` idiom, not the `contract_spike.py` both-paths
# one (a standalone probe that never swaps module state).
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

DEFAULT_SEEDS = [7, 11, 23]

# --- Query tables -----------------------------------------------------------
# Each route table keeps its route's keyword stems (the same tables
# `heuristic.classify` routes on) and carries NONE of the earlier-checked
# routes' stems — quality is checked before churn before sales, so a sales
# paraphrase mentioning "pricing" would route churn, and a churn one
# mentioning "missing" would route quality.

SALES_PARAPHRASES = (
    "Show me sales in Q3",
    "Show me revenue for Q3 2026",
    "How did sales trend in July through September?",
    "Show total revenue by month for Q3",
    "What were sales in August 2026?",
    "Show me the Q3 sales breakdown by segment",
    "How much revenue did we book in September?",
    "Show sales growth across Q3 month by month",
    "What is our revenue trend for the last quarter?",
    "Show me net-new ARR for Q3",
    "How did MRR move over Q3 sales?",
    "Show the sales ramp since the August campaign",
    "What drove revenue growth in Q3?",
    "Show me sales totals for July, August and September",
    "How are sales pacing this quarter?",
    "Show revenue by plan tier for Q3",
    "What was total sales ARR in Q3 2026?",
    "Show me a sales summary for the third quarter",
    "How did new sales ARR trend after the August launch?",
    "Show quarterly revenue and account growth for Q3",
)

CHURN_PARAPHRASES = (
    "Why did churn spike in August?",
    "What caused the August churn spike after the pricing change?",
    "How many accounts cancelled after the pricing change?",
    "Show the churn cohort that cancelled in September",
    "Why did cancellations jump following the August repricing?",
    "Which segment drove the churn spike from the new pricing?",
    "How did the pricing change affect churn in Q3?",
    "What share of cancelled accounts had just adopted a new price?",
    "Explain the churn delta versus baseline after repricing",
    "Who cancelled in August and what plan were they on?",
    "How did cancel rates trend after the August price move?",
    "What was churn in September following the pricing update?",
    "Break down the August churn by segment and price tier",
    "Did the new pricing push Growth accounts to cancel?",
    "How many cancels landed in the August cohort window?",
    "Why is churn elevated since the repricing?",
    "What is the churn rate story for Q3 after the price change?",
    "Which plans saw the most churn once prices moved?",
    "Summarize the pricing-driven churn for August and September",
    "How does the August churn cohort compare by tenure?",
)

QUALITY_PARAPHRASES = (
    "Is the data clean?",
    "Is our data quality good this quarter?",
    "Are there duplicate events in the usage data?",
    "Do we have null amounts in recent usage rows?",
    "Are rows missing from the September usage feed?",
    "How clean is the September usage data?",
    "Report data quality checks for the latest window",
    "Are duplicates inflating usage counts in September?",
    "How many nulls showed up in recent usage events?",
    "Is any usage data missing for the Startup segment?",
    "Check the data for duplicate and null rows",
    "How is data quality trending for event ingestion?",
    "Validate the usage feed is clean of duplicates",
    "Are there quality flags on the September data?",
    "What is missing from the usage table this month?",
    "Show data quality diagnostics for the anomaly window",
    "Is the revenue data clean for Q3?",
    "Audit duplicates and nulls in September usage",
    "How trustworthy is the quality of our event data?",
    "Summarize data quality status including null and duplicate shares",
)

# Distractors must contain NONE of the route stems (checked as substrings,
# exactly like `heuristic.classify` matches them): clean, quality,
# duplicate, null, missing, churn, cancel, pricing, price, sales, revenue,
# show. "draw me a dragon" is the off-catalog golden query (issue #18).
DISTRACTORS = (
    "draw me a dragon",
    "Tell me a joke about robots",
    "What is the weather today?",
    "Write a haiku about the ocean",
    "Who won the football game last night?",
    "Explain how photosynthesis works",
    "What time is it in Tokyo?",
    "Compose a limerick about a cat",
    "How tall is the Eiffel Tower?",
    "Remind me to water the plants tomorrow",
)

# Route stems each table must cover (one per paraphrase) and must avoid
# (distractors must avoid every stem of every route).
SALES_STEMS = ("sales", "revenue", "show")
CHURN_STEMS = ("churn", "cancel", "pricing", "price")
QUALITY_STEMS = ("clean", "quality", "duplicate", "null", "missing")
ALL_STEMS = SALES_STEMS + CHURN_STEMS + QUALITY_STEMS

PATH_TABLES = (
    ("sales", SALES_PARAPHRASES),
    ("churn", CHURN_PARAPHRASES),
    ("quality", QUALITY_PARAPHRASES),
    ("distractor", DISTRACTORS),
)

# Expected exact item-type sets per path: the heuristic's pinned routes
# (sales -> kpi_row + line_chart, churn -> variance_callout + data_table,
# quality -> data_quality_card) and the guard-transport fallback for
# off-catalog distractors (issue #18: the all-sub-threshold cut empties to
# the single-insight_text view).
EXPECTED_TYPES = {
    "sales": {"kpi_row", "line_chart"},
    "churn": {"variance_callout", "data_table"},
    "quality": {"data_quality_card"},
    "distractor": {"insight_text"},
}

FALLBACK_TYPES = ["insight_text"]


# --- Pure helpers (no agent imports: unit-testable on bare python) ----------

def parse_args(argv=None):
    """Eval flags: repeatable `--seed` (default 7 11 23), `--live` (also
    honored via `LIVE=1`) enabling the live-model column."""
    parser = argparse.ArgumentParser(
        description=(
            "Paraphrase generalization eval (issue #89): report-only accuracy / "
            "fallback-rate / unknown-type numbers over seeded and random "
            "sandboxes. Always exits 0 on a completed run."
        )
    )
    parser.add_argument(
        "--seed",
        action="append",
        type=int,
        default=None,
        help=(
            "random-profile sandbox seed (repeatable: --seed 7 --seed 11); "
            f"default: {' '.join(str(s) for s in DEFAULT_SEEDS)}"
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "also run every query through the live decision client "
            "(equivalent to LIVE=1); without a key the live cells read "
            "n/a (no key)"
        ),
    )
    args = parser.parse_args(argv)
    # `append` with a list default would keep appending to the default,
    # so None means "no --seed given" -> the pinned default seed set.
    if args.seed is None:
        args.seed = list(DEFAULT_SEEDS)
    return args


def item_types(a2ui):
    """Item types of an `a2ui` layout dict, in order (tolerates junk)."""
    items = (a2ui or {}).get("items") if isinstance(a2ui, dict) else None
    return [
        item["type"]
        for item in items or []
        if isinstance(item, dict) and isinstance(item.get("type"), str)
    ]


def is_correct(types, expected):
    """Exact expected-set match — order-insensitive, no extras allowed."""
    return set(types) == set(expected)


def is_fallback(types):
    """True when the canvas is the `fallback_layout()`-only view."""
    return list(types) == FALLBACK_TYPES


def unknown_type_count(types, catalog_types):
    """Item types outside the fixed catalog — always 0 (the layout node
    parse-asserts through A2UILayout), counted anyway so the report proves
    it instead of assuming it."""
    allowed = set(catalog_types)
    return sum(1 for item_type in types if item_type not in allowed)


def score_queries(results, expected, catalog_types):
    """Aggregate per-query (correct, fallback, unknown, latency_ms) dicts
    into (accuracy, fallback_rate, median_latency_ms, unknown_total)."""
    total = len(results)
    correct = sum(1 for row in results if is_correct(row["types"], expected))
    fallback = sum(1 for row in results if is_fallback(row["types"]))
    unknown = sum(unknown_type_count(row["types"], catalog_types) for row in results)
    latencies = [row["latency_ms"] for row in results]
    accuracy = correct / total if total else 0.0
    fallback_rate = fallback / total if total else 0.0
    median_latency = float(statistics.median(latencies)) if latencies else 0.0
    return accuracy, fallback_rate, median_latency, unknown


# --- Sandbox builds (subprocess: the generator entrypoint, like the runtime gate) ---

def build_sandbox(db_path, profile, seed=None):
    """Build one sandbox DB through `generate_data.py`'s real entrypoint;
    returns the facts line for eyeballing. Raises on a nonzero exit."""
    command = [
        sys.executable,
        str(GENERATOR),
        "--db-path",
        str(db_path),
        "--profile",
        profile,
    ]
    if seed is not None:
        command += ["--seed", str(seed)]
    result = subprocess.run(
        command,
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=GENERATOR_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"generate_data.py --profile {profile}"
            + (f" --seed {seed}" if seed is not None else "")
            + f" failed:\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


# --- In-process runs (agent imports stay lazy: --help works keyless) --------

def _heuristic_factory():
    """The deterministic decider — no key, no endpoint (the
    `test_graph.py` `_heuristic_client` precedent)."""
    from decision.client import DecisionClient
    from decision.heuristic import HeuristicTransport

    return DecisionClient(HeuristicTransport())


def _live_factory():
    """The production decision client (issues #9/#19 wiring); raises
    RuntimeError at wiring time when no `OPENROUTER_API_KEY` is set —
    the caller records `n/a (no key)` cells instead of failing."""
    from decision.client import build_decision_client

    return build_decision_client()


async def _run_one(query, decision_factory):
    """One query through `read_state -> decide -> layout` on the active
    sandbox; returns (item types, decide latency ms). The query node is
    skipped (its per-slot SQL never changes item types) and so is fill
    (its single text-model call needs a key) — accuracy reads types only."""
    from langchain_core.messages import HumanMessage

    import graph as agent_graph

    original_factory = agent_graph.get_decision_client
    agent_graph.get_decision_client = decision_factory
    try:
        state = {"messages": [HumanMessage(content=query)]}
        read_out = await agent_graph.read_state(state)
        state = {**state, **read_out}
        started = time.monotonic()
        try:
            decide_out = await agent_graph.decide(state)
        finally:
            latency_ms = (time.monotonic() - started) * 1000.0
        state = {**state, **decide_out}
        layout_out = await agent_graph.layout(state)
        return item_types(layout_out.get("a2ui")), latency_ms
    finally:
        agent_graph.get_decision_client = original_factory


async def _run_all(sandboxes, live):
    """Every (sandbox x path x mode) cell: heuristic always, live only
    when requested. Returns {(sandbox_label, path, mode): [per-query
    dicts]} plus the catalog types; live cells without a key are recorded
    as `None` (rendered `n/a (no key)`) without attempting any I/O."""
    import datasets
    import questions

    catalog_types = tuple(questions.CATALOG_TYPES)
    saved_active = datasets.get_active()
    results = {}
    try:
        for label, _db_path, sandbox_id in sandboxes:
            if not datasets.set_active(sandbox_id):
                raise RuntimeError(f"cannot activate sandbox {sandbox_id!r}")
            for path, table in PATH_TABLES:
                expected = EXPECTED_TYPES[path]
                key = (label, path, "heuristic")
                rows = []
                for query in table:
                    types, latency_ms = await _run_one(query, _heuristic_factory)
                    rows.append(
                        {"types": types, "latency_ms": latency_ms}
                    )
                results[key] = (rows, expected)
                if live:
                    live_key = (label, path, "live")
                    if os.getenv("OPENROUTER_API_KEY"):
                        live_rows = []
                        for query in table:
                            types, latency_ms = await _run_one(
                                query, _live_factory
                            )
                            live_rows.append(
                                {"types": types, "latency_ms": latency_ms}
                            )
                        results[live_key] = (live_rows, expected)
                    else:
                        results[live_key] = None
    finally:
        datasets._ACTIVE_ID = saved_active
    return results, catalog_types


def render_report(results, catalog_types, seeds, live):
    """One markdown table (`| path | accuracy | ...` header) plus the
    `fallback rate` and `unknown-type count = 0` summary lines."""
    lines = [
        "# Paraphrase eval report (issue #89)",
        "",
        "Report-only generalization probe: ~20 paraphrases per golden path "
        "(sales / churn / quality) plus 10 distractors, over the seeded "
        "sandbox and `--profile random` sandboxes for "
        f"seeds {', '.join(str(s) for s in seeds)}. "
        "Generated by `python scripts/eval_paraphrase.py`; "
        "exit 0 even when accuracy is low (the numbers inform, never gate).",
        "",
        "Accuracy is the exact expected item-type set match per query; "
        "fallback rate counts `fallback_layout()`-only canvases; "
        "median latency times the decide-node wall clock per query.",
        "",
        "| path | accuracy | sandbox | mode | n | fallback rate | "
        "median latency (ms) | unknown |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    total_queries = 0
    total_unknown = 0
    for (label, path, mode), cell in results.items():
        table = dict(PATH_TABLES)[path]
        if cell is None:
            lines.append(
                f"| {path} | n/a (no key) | {label} | {mode} | {len(table)} | "
                "n/a (no key) | n/a (no key) | n/a (no key) |"
            )
            continue
        rows, expected = cell
        accuracy, fallback_rate, median_latency, unknown = score_queries(
            rows, expected, catalog_types
        )
        total_queries += len(rows)
        total_unknown += unknown
        lines.append(
            f"| {path} | {accuracy:.1%} ({sum(1 for r in rows if is_correct(r['types'], expected))}/{len(rows)}) | "
            f"{label} | {mode} | {len(rows)} | {fallback_rate:.1%} | "
            f"{median_latency:.1f} | {unknown} |"
        )
    lines += [
        "",
        f"unknown-type count = {total_unknown} across all {total_queries} queries "
        "(every emitted item type was in the fixed 6-type catalog).",
        "",
    ]
    void = "heuristic" if not live else "heuristic + live"
    lines.append(
        f"Modes run: {void}"
        + ("" if os.getenv("OPENROUTER_API_KEY") or not live else " (no key: live cells n/a)")
        + "."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    args = parse_args(argv)
    live = bool(args.live or os.getenv("LIVE") == "1")
    seeds = list(args.seed)
    tmpdir = Path(tempfile.mkdtemp(prefix="paraphrase-eval-"))
    sandboxes = []
    try:
        seeded_path = tmpdir / "seeded.db"
        print(f"building seeded sandbox: {seeded_path}")
        print(" ", build_sandbox(seeded_path, "seeded"))
        sandboxes.append(("seeded", seeded_path, "eval-seeded"))
        import datasets

        datasets.register("eval-seeded", seeded_path, "sample")
        for seed in seeds:
            random_path = tmpdir / f"random-{seed}.db"
            print(f"building random sandbox (seed {seed}): {random_path}")
            print(" ", build_sandbox(random_path, "random", seed))
            sandbox_id = f"eval-random-{seed}"
            datasets.register(sandbox_id, random_path, "sample")
            sandboxes.append((f"seed-{seed}", random_path, sandbox_id))
        results, catalog_types = asyncio.run(_run_all(sandboxes, live))
    finally:
        import datasets

        datasets._ACTIVE_ID = None
    report = render_report(results, catalog_types, seeds, live)
    print()
    print(report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report + "\n", encoding="utf-8")
    print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

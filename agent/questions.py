"""Natural-language domain logic for the decide node (issue #10).

`resolve_window` turns the user's message into the display window every
node works on: a regex scan for quarters (Q1..Q4) and month names with an
optional explicit year, anchored on the data's own latest event day —
never a wall clock, so the same message + same seed always yields the same
ISO range. An explicit year is honored verbatim even when it lies outside
the data (the user asked for that window; the components just render empty
series); a year-omitted period that is still incomplete relative to
data_end backs off one year (the latest complete occurrence). No match
falls back to the complete quarter containing data_end — "show me sales"
still gets a sane, deliberate window. Model-inferred windows were
deliberately rejected: non-deterministic, an extra round trip, and issue
#10's acceptance pins the exact ISO range for the seeded Q3 query.

`build_question_set` assembles the registered catalog question set (PRD
§8/§9): exactly one ChoiceQuestion over the six catalog component types
(its answer's per-option probabilities are what the layout node's
threshold cut consumes) plus the two Noul questions (real change vs. data
problem, anomaly worth calling out) and one Score question (data-quality
confidence). Every prompt quotes the user message VERBATIM and inlines a
compact KPI/DQ summary from read_state's snapshot — transports key answers
by question id, so per-query information can only enter through the
prompt. The set is capped at MAX_DECISION_QUESTIONS (7, the decide node's
fan-out budget, PRD's "≤7 parallel decision calls"); the fixed four stay
well inside it and the assert guards future edits.

Stdlib only — the mirrors catalog.py/decision/schemas.py leaf contract, so
the graph node imports it inside the async hot path without dragging the
model stack along.
"""

import re
from datetime import date, timedelta

try:  # package-style entry (import agent.questions from the repo root)
    from agent.decision.schemas import (
        ChoiceQuestion,
        NoulQuestion,
        ScoreQuestion,
    )
except ModuleNotFoundError:  # script-style entry (import questions from agent/)
    from decision.schemas import (
        ChoiceQuestion,
        NoulQuestion,
        ScoreQuestion,
    )

# The fixed six-component catalog (PRD §7), in catalog.py's discriminated
# union order — the ChoiceQuestion's options and the layout node's draft
# both key on exactly these tags.
CATALOG_TYPES = (
    "kpi_row",
    "line_chart",
    "variance_callout",
    "data_table",
    "data_quality_card",
    "insight_text",
)

# Decide-node fan-out budget (PRD: at most 7 parallel decision calls).
MAX_DECISION_QUESTIONS = 7

# Fixed question ids — decide stamps decisions with them, the layout node
# looks the catalog answer up by id, and transports (Mock/Heuristic) key
# per-question behavior off the prompts built here.
CATALOG_QUESTION_ID = "catalog_components"
REAL_CHANGE_QUESTION_ID = "real_change_vs_data_problem"
ANOMALY_QUESTION_ID = "anomaly_callout"
DQ_CONFIDENCE_QUESTION_ID = "dq_confidence"

# Quarters (Q1..Q4, optional trailing year) and month names (full or
# 3-letter, optional trailing year). Alternation is longest-first so
# "sept" wins over "sep", and the \b guards keep "mar" out of "march".
_QUARTER_RE = re.compile(r"\bq([1-4])\b(?:\s*[,']?\s*(20\d{2}))?", re.IGNORECASE)
_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sept", "sep",
    "oct", "nov", "dec",
)
_MONTH_RE = re.compile(
    r"\b(" + "|".join(_MONTH_NAMES) + r")\b(?:\s*[,']?\s*(20\d{2}))?",
    re.IGNORECASE,
)
_MONTH_NUMBERS = {
    name: index + 1
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        )
    )
    for name in names
}

# Question-set size guard: the registered catalog set must stay inside the
# decide node's fan-out budget.
assert len(CATALOG_TYPES) == 6


def _quarter_bounds(year, quarter):
    """(first, last) ISO dates of quarter `quarter` (1-4) of `year`."""
    first_month = (quarter - 1) * 3 + 1
    last_month = first_month + 2
    start = date(year, first_month, 1)
    end = date(year, last_month, _month_end(year, last_month))
    return start.isoformat(), end.isoformat()


def _month_end(year, month):
    """Last calendar day of `month` in `year` (leap years included)."""
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def _month_bounds(year, month):
    """(first, last) ISO dates of `month` in `year`."""
    return (
        date(year, month, 1).isoformat(),
        date(year, month, _month_end(year, month)).isoformat(),
    )


def quarter_of_day(day):
    """(first, last) ISO dates of the quarter containing `day` (a date)."""
    return _quarter_bounds(day.year, (day.month - 1) // 3 + 1)


def previous_quarter(window):
    """The quarter immediately before `window`'s start quarter — read_state's
    comparison window for KPI deltas. Anchoring on the start's own quarter
    (not start minus one day, which can land in the same quarter) keeps the
    result correct for any window shape: month windows, mid-quarter starts,
    quarter-aligned ones alike."""
    start = date.fromisoformat(window[0])
    quarter_start, _ = quarter_of_day(start)
    return quarter_of_day(date.fromisoformat(quarter_start) - timedelta(days=1))


def resolve_window(text, *, data_end):
    """Resolve the user's message to a display window of ISO dates.

    text: the raw user message; scanned for a quarter (Q1..Q4) or a month
        name, each with an optional explicit year — the earliest match in
        the text wins.
    data_end: the data's latest event day (ISO string) or None — the anchor
        for year resolution and the fallback window. None (no readable DB)
        falls back to today's wall clock so the graph still resolves.

    Year rules: an explicit year is honored verbatim (even outside the
    data); a year-omitted period anchored on data_end backs off one year
    when its first year's window is still incomplete relative to data_end
    (the latest complete occurrence of that period). With no period in the
    message at all, the fallback is the complete quarter containing
    data_end (the quarter preceding it when data_end sits mid-quarter).
    """
    anchor = date.fromisoformat(data_end) if data_end else date.today()
    text = text or ""

    matches = []
    quarter_match = _QUARTER_RE.search(text)
    if quarter_match:
        matches.append((quarter_match.start(), "quarter", quarter_match))
    month_match = _MONTH_RE.search(text)
    if month_match:
        matches.append((month_match.start(), "month", month_match))
    if not matches:
        start, end = quarter_of_day(anchor)
        if end > anchor.isoformat():
            # data_end sits mid-quarter: the fallback never serves a partial
            # window, so the latest COMPLETE quarter is the one before it.
            start, end = previous_quarter((start,))
        return start, end

    _, kind, match = min(matches, key=lambda entry: entry[0])
    explicit_year = int(match.group(2)) if match.group(2) else None
    if kind == "quarter":
        quarter = int(match.group(1))
        bounds = _quarter_bounds
    else:
        quarter = _MONTH_NUMBERS[match.group(1).lower()]
        bounds = _month_bounds
    year = explicit_year if explicit_year is not None else anchor.year
    start, end = bounds(year, quarter)
    if explicit_year is None and end > anchor.isoformat():
        # Unpinned and still incomplete relative to the data: back off one
        # year to the latest complete occurrence of this period.
        start, end = bounds(year - 1, quarter)
    return start, end


def compact_snapshot_summary(snapshot):
    """One-line KPI/DQ summary embedded into every question prompt — the
    only channel through which read_state's aggregates reach the decider
    (transports key answers by question id, so per-query context must ride
    in the prompt). Values are read defensively: a degraded snapshot (no
    readable DB) still yields a usable prompt."""
    if not snapshot:
        return "no data snapshot available"
    window = snapshot.get("window") or {}
    parts = []
    if window.get("range"):
        parts.append(f"window {window['range']}")
    kpis = snapshot.get("kpis")
    if isinstance(kpis, dict) and kpis:
        parts.append(
            "net_new_arr=${:,.0f}".format(kpis.get("net_new_arr") or 0)
        )
        parts.append("mrr=${:,.0f}".format(kpis.get("mrr") or 0))
        nrr = kpis.get("nrr")
        if isinstance(nrr, (int, float)) and not isinstance(nrr, bool):
            parts.append(f"nrr={nrr:.2f}")
        else:
            parts.append("nrr=0.00")
        parts.append(f"accounts={kpis.get('accounts') or 0}")
    churn = snapshot.get("churn")
    if isinstance(churn, dict) and churn:
        parts.append(f"churn_delta={_fmt_ratio(churn.get('delta'))}")
    checks = snapshot.get("dq_checks")
    if isinstance(checks, list) and checks:
        statuses = ", ".join(
            f"{row.get('name')}={row.get('status')}"
            for row in checks
            if isinstance(row, dict)
        )
        if statuses:
            parts.append(f"data quality: {statuses}")
    return "; ".join(parts) if parts else "no data snapshot available"


def _fmt_ratio(value):
    """+/- percentage form of a signed ratio; non-numeric reads 'n/a'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:+.1%}"
    return "n/a"


def build_question_set(query_text, snapshot):
    """The registered catalog question set (exactly four questions) for one
    user message + read_state snapshot — the decide node's fan-out input.

    Every prompt quotes the message verbatim and inlines the compact KPI/DQ
    summary, so transports that never read state still decide per query."""
    summary = compact_snapshot_summary(snapshot)
    context = (
        f'The user asked: "{query_text}"\n'
        f"Data context: {summary}\n"
    )

    question_set = [
        ChoiceQuestion(
            id=CATALOG_QUESTION_ID,
            prompt=(
                context
                + "Which catalog components does the answer to this query "
                "need? Give a probability in [0, 1] for each option."
            ),
            options=list(CATALOG_TYPES),
        ),
        NoulQuestion(
            id=REAL_CHANGE_QUESTION_ID,
            prompt=(
                context
                + "Is the observed movement best explained by a real business "
                "change rather than a data-quality problem? Answer yes or no."
            ),
        ),
        NoulQuestion(
            id=ANOMALY_QUESTION_ID,
            prompt=(
                context
                + "Is there an anomaly worth calling out to the user in this "
                "window? Answer yes or no."
            ),
        ),
        ScoreQuestion(
            id=DQ_CONFIDENCE_QUESTION_ID,
            prompt=(
                context
                + "Score in [0, 1]: how confident are you that the data for "
                "this window is clean?"
            ),
        ),
    ]
    # The decide node fans out over this set; the registered set must stay
    # inside the PRD's parallelism budget (guard future edits, not the
    # constants above — those are pinned by their own tests).
    assert 1 <= len(question_set) <= MAX_DECISION_QUESTIONS
    return question_set

"""Server-side A2UI layout schema for the fixed 6-component catalog (issue #7).

Everything the agent ever renders in the dashboard canvas is one of six
component types — kpi_row, line_chart, variance_callout, data_table,
data_quality_card, insight_text (PRD §7) — wrapped in item dicts of shape
{"type", "props"} inside the top-level layout {"v", "layout", "items",
"slotCopy"} (PRD §10 wire format). This module is the server-side mirror:
every draft layout validates through A2UILayout BEFORE it reaches the
frontend, so an off-catalog "unicorn" type or a misspelled slot fails fast
here instead of silently rendering an empty component in the browser.

Strictness by design: every model sets extra="forbid" over the frozen
catalog — an unknown key (a misspelled "seriess", an extra item field, an
unfamiliar top-level key) raises ValidationError rather than being ignored,
because in a fixed 6-component catalog an unrecognized key can only be a
bug or an injection attempt. Items are a union discriminated on the "type"
tag, so one schema covers all six components, only one sub-model can claim
each tag, and every error names the offending type (or slot).

filter_layout(items, probs, threshold) is the probability cut between the
decider's per-component probabilities and the final layout:

  * an item survives iff probs.get(item.type, 0.0) >= threshold — the
    issue drops on p < threshold, so a component exactly at the line
    survives;
  * a component the decider never scored reads as 0.0 and is dropped —
    fail-safe, an unendorsed component must not reach the frontend;
  * when nothing survives (or the input list is empty) the helper returns
    the insight_text-only fallback layout built from FALLBACK_TITLE /
    FALLBACK_BODY — the same single-component shape the frontend's
    fallback card expects.

probs values and threshold are validated fail-loud (number, non-bool, in
[0.0, 1.0]) via _require_unit_interval; resolving threshold from the
DECISION_THRESHOLD env variable is the caller's job (issue #19) — the
helper takes it explicitly. items are validated through A2UILayout inside
the helper too, so malformed props raise ValidationError from here as
well, whether they arrive as raw dicts or pre-built item models.

v and layout are version guards, not free fields: Literal[1] and
Literal["grid"] — the only protocol version defined (PRD §10). A draft
claiming v=2 fails validation instead of passing through to a frontend
that cannot read it.

Additive by contract: imports only stdlib + pydantic — no db/graph/server
dependency — so later consumers (#8 DecisionClient, #10 graph layout node,
#11 frontend Zod mirror) can import it from any context.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# --- Component props (field names verbatim from PRD §7/§10) -------------------


class KpiEntry(BaseModel):
    """One KPI tile: {label, value, delta} (PRD §7)."""

    model_config = ConfigDict(extra="forbid")

    label: str
    value: int | float
    delta: float


class KpiRowProps(BaseModel):
    """kpi_row props: 3–4 KPI tiles (PRD §7 "3–4 KPIs")."""

    model_config = ConfigDict(extra="forbid")

    kpis: list[KpiEntry] = Field(min_length=3, max_length=4)


class LineChartProps(BaseModel):
    """line_chart props: series + range required, annotations optional —
    the slot exists in the PRD §7 list but not in the §10 example."""

    model_config = ConfigDict(extra="forbid")

    series: str
    range: str
    annotations: list[str] = []


class VarianceCalloutProps(BaseModel):
    """variance_callout props: metric + delta required (a variance callout
    without a delta is meaningless, and the §10 example always carries
    both); baseline / cause / cta optional."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    delta: float
    baseline: str | None = None
    cause: str | None = None
    cta: str | None = None


class DataTableProps(BaseModel):
    """data_table props: columns + rows (≤50) required, filters optional
    (PRD §7 "rows ≤50"). The PRD names no row object shape, so rows stay
    generic dicts and columns plain strings — the minimal faithful reading."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[dict[str, Any]] = Field(max_length=50)
    filters: list[str] = []


class CheckResult(BaseModel):
    """One data-quality check row: exactly the {name, status, score} shape
    quality.run_checks returns (status ok | warn | fail)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["ok", "warn", "fail"]
    score: float


class DataQualityCardProps(BaseModel):
    """data_quality_card props: the run_checks rows it surfaces (PRD §7).
    Issue #17 adds two optional agent-stamped fields: data_problem is the
    data_problem noul's probability (the frontend banner gate reads it —
    absent keeps the gate shut on fixture/older renders) and
    data_problem_detail is the narrated evidence line (window + segment)
    the banner and the card's summary slot repeat."""

    model_config = ConfigDict(extra="forbid")

    checks: list[CheckResult]
    data_problem: float | None = None
    data_problem_detail: str | None = None


class InsightTextProps(BaseModel):
    """insight_text props: title + body narrative block (PRD §7)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str


# --- Catalog items: {"type": <tag>, "props": <Props models>} ------------------


class KpiRowItem(BaseModel):
    """kpi_row item wrapper."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["kpi_row"]
    props: KpiRowProps


class LineChartItem(BaseModel):
    """line_chart item wrapper."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["line_chart"]
    props: LineChartProps


class VarianceCalloutItem(BaseModel):
    """variance_callout item wrapper."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["variance_callout"]
    props: VarianceCalloutProps


class DataTableItem(BaseModel):
    """data_table item wrapper."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["data_table"]
    props: DataTableProps


class DataQualityCardItem(BaseModel):
    """data_quality_card item wrapper."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["data_quality_card"]
    props: DataQualityCardProps


class InsightTextItem(BaseModel):
    """insight_text item wrapper."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["insight_text"]
    props: InsightTextProps


# One discriminated union over the fixed catalog: an unknown "type" tag
# fails with union_tag_invalid naming the tag — the exact guarantee issue
# #7's second acceptance clause pins.
LayoutItem = Annotated[
    Union[
        KpiRowItem,
        LineChartItem,
        VarianceCalloutItem,
        DataTableItem,
        DataQualityCardItem,
        InsightTextItem,
    ],
    Field(discriminator="type"),
]


class A2UILayout(BaseModel):
    """PRD §10 wire format: {"v": 1, "layout": "grid", "items": [...],
    "slotCopy": {...}} — the one schema every layout draft must survive
    before reaching the frontend."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    layout: Literal["grid"] = "grid"
    items: list[LayoutItem] = []
    slotCopy: dict[str, str] = {}


# --- Fallback layout ----------------------------------------------------------


# Fallback copy for the nothing-survived cut — module constants so tests and
# callers assert against one source of truth instead of string duplicates.
FALLBACK_TITLE = "Nothing to visualize"
FALLBACK_BODY = (
    "No dashboard component scored high enough on this query to be shown "
    "with confidence. Try rephrasing the question."
)


def fallback_layout():
    """The single-insight_text default layout (issue #7 acceptance clause 3):
    what filter_layout returns when the threshold cut empties the canvas.
    Public since issue #18: the graph's layout/fill nodes and the off-catalog
    safety tests consume it as the module's fallback surface."""
    return A2UILayout(
        items=[
            InsightTextItem(
                type="insight_text",
                props=InsightTextProps(title=FALLBACK_TITLE, body=FALLBACK_BODY),
            )
        ]
    )


# --- Threshold cut ------------------------------------------------------------


def _require_unit_interval(value, label):
    """Reject bools (an int subclass), non-numbers and anything outside
    [0.0, 1.0] (NaN fails the comparison and is rejected too), returning
    the value — the fail-loud idiom of queries._require_int_at_least
    applied to probabilities."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{label} must be a number in [0.0, 1.0], got {value!r}")
    return value


def filter_layout(items, probs, threshold):
    """Cut a layout draft down to the components the decider endorsed,
    returning an A2UILayout (issue #7 acceptance clause 3).

    items: the draft's item list — raw dicts or already-validated item
        models; validated through A2UILayout first, so malformed props or
        an off-catalog type raise ValidationError from here.
    probs: {component type -> probability} — the decider's answer to "Which
        catalog components does this query need?" (PRD §9), keyed by type.
        A component the decider never scored reads as 0.0 and is dropped:
        fail-safe, an unendorsed component must not reach the frontend.
    threshold: the endorsement line — keep an item iff p >= threshold (the
        issue drops on p < threshold, so a p exactly at the line survives).

    Returns an A2UILayout; when no item survives — or the input list is
    empty — the insight_text-only fallback layout is returned.
    """
    _require_unit_interval(threshold, "threshold")
    if not isinstance(probs, dict):
        raise ValueError(
            f"probs must be a mapping of component type -> probability, got {probs!r}"
        )
    for key in probs:
        if not isinstance(key, str):
            raise ValueError(f"probs keys must be component-type strings, got {key!r}")
    for key, value in probs.items():
        _require_unit_interval(value, f"probs[{key!r}]")

    validated = A2UILayout.model_validate({"items": items})
    kept = [
        item for item in validated.items if probs.get(item.type, 0.0) >= threshold
    ]
    if not kept:
        return fallback_layout()
    return A2UILayout(
        v=validated.v, layout=validated.layout, items=kept, slotCopy=validated.slotCopy
    )

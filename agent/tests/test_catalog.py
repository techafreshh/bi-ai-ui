"""Acceptance-contract tests for the A2UI catalog layout schemas (issue #7).

One test per acceptance clause of issue #7 (plus the fail-loud probs /
threshold validation and the PRD §7 shape pins the plan commits to), each
with a docstring naming the clause it proves. Pure schema tests: catalog.py
imports nothing from this repo but stdlib + pydantic, so nothing here
touches the DB, the server or the graph — the only imported repo modules
are catalog itself and quality, whose run_checks row shape the
data_quality_card must mirror.
"""

import pytest
from pydantic import ValidationError

import catalog
import quality


def _kpi(label, value, delta):
    """One KPI tile dict; value stays an int for the PRD §10 example."""
    return {"label": label, "value": value, "delta": delta}


def _default_kpis():
    """The three KPI tiles every kpi_row draft below reuses."""
    return [
        _kpi("Net-new ARR", 1200000, 0.18),
        _kpi("MRR", 240000, 0.04),
        _kpi("NRR", 1.08, 0.02),
    ]


def _kpi_row_dict(kpis=None):
    """A kpi_row draft item (defaults to three KPIs, the PRD §7 lower bound)."""
    return {"type": "kpi_row", "props": {"kpis": _default_kpis() if kpis is None else kpis}}


def _line_chart_dict():
    """A line_chart draft item, straight from the PRD §10 example."""
    return {
        "type": "line_chart",
        "props": {"series": "arr_daily", "range": "2026-07-01..2026-09-30"},
    }


def _data_table_dict(rows=None):
    """A data_table draft item with two string columns and small rows."""
    if rows is None:
        rows = [{"account": "Acme", "mrr": 999}, {"account": "Globex", "mrr": 1234}]
    return {
        "type": "data_table",
        "props": {"columns": ["account", "mrr"], "rows": rows},
    }


def test_valid_layout_round_trips():
    """Acceptance clause 1: a layout dict containing a kpi_row and a
    line_chart with valid props parses to A2UILayout and round-trips
    .model_dump() without error — with the PRD §10 example's integer KPI
    value (1200000) surviving the dump as an int, not a float."""
    layout = catalog.A2UILayout.model_validate(
        {
            "v": 1,
            "layout": "grid",
            "items": [_kpi_row_dict(), _line_chart_dict()],
            "slotCopy": {"kpis.title": "Q3 revenue took off"},
        }
    )
    assert isinstance(layout, catalog.A2UILayout)
    dumped = layout.model_dump()
    kpi_value = dumped["items"][0]["props"]["kpis"][0]["value"]
    assert kpi_value == 1200000 and isinstance(kpi_value, int)
    assert catalog.A2UILayout.model_validate(dumped) == layout


def test_unknown_type_error_names_type():
    """Acceptance clause 2: validating an item with {"type": "unicorn"} —
    an off-catalog type — raises ValidationError whose message names the
    offending type."""
    with pytest.raises(ValidationError) as exc_info:
        catalog.A2UILayout.model_validate({"items": [{"type": "unicorn"}]})
    assert "unicorn" in str(exc_info.value)


@pytest.mark.parametrize(
    "hostile_type", ["dragon", "chart", "table", "GRID", "kpi rows", ""]
)
def test_off_catalog_type_puns_are_rejected(hostile_type):
    """Issue #18: type tags that pun on catalog words — a substring
    ("chart"/"table"), a case variant ("GRID"), a plural phrase, an empty
    string — all fail the discriminated union. Nothing outside the fixed
    6-tag set can ever validate, so an off-catalog ask cannot render an
    unknown component even from a hostile payload."""
    with pytest.raises(ValidationError):
        catalog.A2UILayout.model_validate(
            {"items": [{"type": hostile_type, "props": {}}]}
        )


def test_fallback_layout_contract():
    """Issue #18: fallback_layout() — public since the graph's layout/fill
    nodes consume it as the fallback surface — returns exactly the default
    view: v=1, layout 'grid', one insight_text item carrying the module
    fallback copy (titled, non-empty body), and its dump re-validates."""
    result = catalog.fallback_layout()
    assert result.v == 1
    assert result.layout == "grid"
    assert [item.type for item in result.items] == ["insight_text"]
    assert len(result.items) == 1
    assert result.items[0].props.title == catalog.FALLBACK_TITLE
    assert result.items[0].props.body == catalog.FALLBACK_BODY
    assert result.items[0].props.body  # non-empty by contract
    assert catalog.A2UILayout.model_validate(result.model_dump()) == result


def test_filter_layout_keeps_only_above_threshold():
    """Acceptance clause 3 (keep branch): given items (kpi_row, p=0.91) and
    (data_table, p=0.31) with threshold 0.6, only the kpi_row remains, its
    props intact; a component exactly at the threshold also survives, since
    the issue drops on p < threshold only."""
    result = catalog.filter_layout(
        [_kpi_row_dict(), _data_table_dict()],
        {"kpi_row": 0.91, "data_table": 0.31},
        0.6,
    )
    assert isinstance(result, catalog.A2UILayout)
    assert [item.type for item in result.items] == ["kpi_row"]
    assert [kpi.label for kpi in result.items[0].props.kpis] == [
        "Net-new ARR",
        "MRR",
        "NRR",
    ]

    at_line = catalog.filter_layout([_kpi_row_dict()], {"kpi_row": 0.6}, 0.6)
    assert [item.type for item in at_line.items] == ["kpi_row"]


def test_filter_layout_falls_back_to_single_insight_text():
    """Acceptance clause 3 (fallback branch): when both probabilities fall
    below the threshold, filter_layout returns a layout holding exactly one
    insight_text item whose title/body equal the module constants — and the
    same fallback covers an empty input list (nothing to keep)."""
    result = catalog.filter_layout(
        [_kpi_row_dict(), _data_table_dict()],
        {"kpi_row": 0.3, "data_table": 0.3},
        0.6,
    )
    assert isinstance(result, catalog.A2UILayout)
    assert len(result.items) == 1
    only = result.items[0]
    assert only.type == "insight_text"
    assert only.props.title == catalog.FALLBACK_TITLE
    assert only.props.body == catalog.FALLBACK_BODY

    empty = catalog.filter_layout([], {"kpi_row": 0.9}, 0.6)
    assert [item.type for item in empty.items] == ["insight_text"]
    assert empty.items[0].props.title == catalog.FALLBACK_TITLE


def test_variance_callout_requires_metric():
    """Acceptance clause 4: variance_callout props missing `metric` raise a
    ValidationError whose message names `metric`; the full PRD §10 callout
    (metric, delta, baseline, cause, cta) validates."""
    with pytest.raises(ValidationError) as exc_info:
        catalog.VarianceCalloutProps.model_validate({"delta": 0.42})
    assert "metric" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        catalog.A2UILayout.model_validate(
            {"items": [{"type": "variance_callout", "props": {"delta": 0.42}}]}
        )
    assert "metric" in str(exc_info.value)

    full = catalog.VarianceCalloutProps.model_validate(
        {
            "metric": "MRR churn",
            "delta": 0.42,
            "baseline": "0.5% monthly",
            "cause": "pricing change Aug 10",
            "cta": "investigate_churn_cohort",
        }
    )
    assert full.metric == "MRR churn"


def test_filter_layout_validates_inputs():
    """Fail-loud inputs: threshold must be a non-bool number in [0.0, 1.0]
    and probs a {str: number} mapping (ValueError otherwise); a component
    absent from probs reads as 0.0 and is dropped (fail-safe); malformed
    items raise ValidationError from inside the helper."""
    row, table = _kpi_row_dict(), _data_table_dict()

    # Missing-probability type: the decider never scored data_table -> gone.
    result = catalog.filter_layout([row, table], {"kpi_row": 0.91}, 0.6)
    assert [item.type for item in result.items] == ["kpi_row"]

    for bad_threshold in (True, "0.5", None, 1.5, -0.1):
        with pytest.raises(ValueError):
            catalog.filter_layout([row], {"kpi_row": 0.9}, bad_threshold)

    for bad_probs in (
        None,
        [{"kpi_row": 0.9}],
        {42: 0.9},
        {"kpi_row": "high"},
        {"kpi_row": True},
        {"kpi_row": 1.5},
    ):
        with pytest.raises(ValueError):
            catalog.filter_layout([row], bad_probs, 0.6)

    with pytest.raises(ValidationError):
        catalog.filter_layout([row, {"type": "unicorn"}], {"kpi_row": 0.9}, 0.6)
    with pytest.raises(ValidationError):
        # One KPI is below the PRD §7 three-KPI minimum.
        catalog.filter_layout([{"type": "kpi_row", "props": {"kpis": [_kpi("ARR", 1, 0.0)]}}], {"kpi_row": 0.9}, 0.6)


def test_prd_shape_pins():
    """PRD §7 shape pins: kpi_row holds exactly 3–4 KPIs (2 and 5 rejected);
    data_table holds at most 50 rows (50 itself accepted); data_quality_card
    accepts exactly a quality.run_checks-shaped row, no DB involved; one
    layout containing all six catalog components validates and dumps."""
    with pytest.raises(ValidationError):
        catalog.A2UILayout.model_validate({"items": [_kpi_row_dict(_default_kpis()[:2])]})
    with pytest.raises(ValidationError):
        catalog.A2UILayout.model_validate(
            {"items": [_kpi_row_dict([_kpi(f"K{i}", 1, 0.0) for i in range(5)])]}
        )

    with pytest.raises(ValidationError):
        catalog.A2UILayout.model_validate(
            {"items": [_data_table_dict(rows=[{"id": i} for i in range(51)])]}
        )
    fifty = catalog.A2UILayout.model_validate(
        {"items": [_data_table_dict(rows=[{"id": i} for i in range(50)])]}
    )
    assert len(fifty.items[0].props.rows) == 50

    # The row shape quality.run_checks documents (quality._CHECK_NAMES[1] is
    # "duplicates") slots straight into the card — without opening the DB.
    check_row = {"name": quality._CHECK_NAMES[1], "status": "fail", "score": 0.8}
    dq = catalog.A2UILayout.model_validate(
        {"items": [{"type": "data_quality_card", "props": {"checks": [check_row]}}]}
    )
    assert dq.items[0].props.checks[0].name == "duplicates"

    everything = catalog.A2UILayout.model_validate(
        {
            "items": [
                _kpi_row_dict(),
                _line_chart_dict(),
                {
                    "type": "variance_callout",
                    "props": {
                        "metric": "MRR churn",
                        "delta": 0.42,
                        "baseline": "0.5% monthly",
                        "cause": "pricing change Aug 10",
                        "cta": "investigate_churn_cohort",
                    },
                },
                _data_table_dict(),
                {"type": "data_quality_card", "props": {"checks": [check_row]}},
                {"type": "insight_text", "props": {"title": "T", "body": "B"}},
            ]
        }
    )
    assert [item.type for item in everything.items] == [
        "kpi_row",
        "line_chart",
        "variance_callout",
        "data_table",
        "data_quality_card",
        "insight_text",
    ]
    assert catalog.A2UILayout.model_validate(everything.model_dump()) == everything

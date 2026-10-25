"""Component catalog + A2UI layout schemas + layout composition (PRD §6/§10).

The agent never invents markup: ``compose_layout`` derives items from decision
picks, fills props from the query node, validates the whole layout against
Pydantic (mirrored by Zod in ``web/src/lib/a2ui.ts``), and degrades to a
single ``insight_text`` fallback when nothing survives the threshold.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

# The six catalog components (PRD §7). Everything the UI can render.
COMPONENT_IDS = (
    "kpi_row",
    "line_chart",
    "variance_callout",
    "data_table",
    "data_quality_card",
    "insight_text",
)

# Human labels for chat replies / screenshots / drawer copy.
COMPONENT_LABELS = {
    "kpi_row": "KPI row",
    "line_chart": "Line chart",
    "variance_callout": "Variance callout",
    "data_table": "Data table",
    "data_quality_card": "Data quality card",
    "insight_text": "Insight text",
}

# Props every item of a type must carry (validated below; extra keys allowed).
REQUIRED_PROPS: dict[str, tuple[str, ...]] = {
    "kpi_row": ("kpis",),
    "line_chart": ("series", "range", "points"),
    "variance_callout": ("metric", "delta", "cause"),
    "data_table": ("columns", "rows"),
    "data_quality_card": ("checks",),
    "insight_text": ("title", "body"),
}

MAX_TABLE_ROWS = 50  # catalog cap (PRD §7: rows ≤ 50)


class CatalogError(ValueError):
    """A layout was built with an unknown component or missing props."""


class A2UIItem(BaseModel):
    """One catalog component instance in the layout."""

    type: str
    props: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_catalog_member(self) -> "A2UIItem":
        if self.type not in COMPONENT_IDS:
            raise CatalogError(f"unknown component type: {self.type!r}")
        missing = [k for k in REQUIRED_PROPS[self.type] if k not in self.props]
        if missing:
            raise CatalogError(f"{self.type}: missing props {missing}")
        if self.type == "kpi_row":
            kpis = self.props["kpis"]
            if not isinstance(kpis, list) or not 3 <= len(kpis) <= 4:
                raise CatalogError("kpi_row: kpis must list 3–4 tiles")
            for kpi in kpis:
                if not isinstance(kpi, dict) or "label" not in kpi or "value" not in kpi:
                    raise CatalogError("kpi_row: each kpi needs label and value")
        if self.type == "line_chart":
            points = self.props["points"]
            if not isinstance(points, list) or not points:
                raise CatalogError("line_chart: points must be a non-empty list")
        if self.type == "data_table":
            rows = self.props["rows"]
            if not isinstance(rows, list):
                raise CatalogError("data_table: rows must be a list")
            if len(rows) > MAX_TABLE_ROWS:
                raise CatalogError(f"data_table: exceeds {MAX_TABLE_ROWS} rows")
            if not self.props["columns"]:
                raise CatalogError("data_table: columns must be non-empty")
        if self.type == "data_quality_card":
            checks = self.props["checks"]
            if not isinstance(checks, list) or not checks:
                raise CatalogError("data_quality_card: checks must be a non-empty list")
            for check in checks:
                if not isinstance(check, dict) or not {"name", "status", "score"} <= set(check):
                    raise CatalogError("data_quality_card: each check needs name/status/score")
        return self


class A2UILayout(BaseModel):
    """The full layout envelope shipped to the frontend as ``state.a2ui``."""

    v: int = 1
    layout: str = "grid"
    route: str
    items: list[A2UIItem]
    slotCopy: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid", "populate_by_name": True}


def validate_layout(raw: dict) -> A2UILayout:
    """Validate an untrusted layout dict (raises pydantic.ValidationError)."""
    return A2UILayout.model_validate(raw)


def compose_layout(
    route: str,
    decisions: list[dict],
    props_by_component: dict[str, dict[str, Any]],
    narrative: str,
) -> tuple[dict, dict]:
    """Build the validated layout + composition meta.

    Rules (PRD threshold + default fallback):
    - only components the ``component_choice`` decision picked (p ≥ threshold)
      become items, in catalog order;
    - props come from ``props_by_component``; ``insight_text`` gets the
      narrative via a ``slotCopy`` entry;
    - when nothing survives, the layout is the single fallback
      ``insight_text`` item and ``meta["fallback"]`` is True.

    Returns ``(layout_dict, meta)`` with
    ``meta = {"picked": [...], "dropped": [...], "fallback": bool}``.
    """
    choice = next((d for d in decisions if d.get("id") == "component_choice"), None)
    picked: list[str] = list((choice or {}).get("pick") or [])
    candidates = [c for c in COMPONENT_IDS if c in picked and c in props_by_component]
    dropped = [c for c in COMPONENT_IDS if c in picked and c not in props_by_component]

    items: list[dict] = []
    for comp in candidates:
        items.append({"type": comp, "props": props_by_component[comp]})

    fallback = not items
    if fallback:
        items = [{
            "type": "insight_text",
            "props": {"title": "Outside the dashboard catalog", "body": narrative},
        }]
    layout = {
        "v": 1,
        "layout": "grid",
        "route": route,
        "items": items,
        "slotCopy": {},
    }
    if "insight_text" in [i["type"] for i in items]:
        layout["slotCopy"] = {"insight_text.body": narrative}
    validated = validate_layout(layout)
    return validated.model_dump(), {
        "picked": picked,
        "dropped": dropped,
        "fallback": fallback,
    }

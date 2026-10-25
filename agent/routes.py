"""Query routing + per-route demo policy (PRD "choose, don't invent").

Routing is a deterministic keyword classifier on six routes, not a model call:
layout selection is the agent's state-machine logic and must be reproducible
for the probe suite (the text model only ever writes narrative copy).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

RouteId = str  # one of ROUTES

ROUTES = (
    "q3_sales",
    "churn_why",
    "data_clean",
    "investigate_churn",
    "report_issue",
    "off_catalog",
)

CATALOG_COMPONENTS = (
    "kpi_row",
    "line_chart",
    "variance_callout",
    "data_table",
    "data_quality_card",
    "insight_text",
)

# Specificity-ordered: investigate/report phrasings must win over the generic
# business keywords they contain ("Investigate the churn cohort" is NOT
# churn_why; "Report an issue with the chart" is NOT q3_sales).
_ROUTE_KEYWORDS: tuple[tuple[RouteId, tuple[str, ...]], ...] = (
    (
        "investigate_churn",
        (
            "investigate",
            "drill into",
            "drill down",
            "churn cohort",
            "cohort detail",
            "who churned",
        ),
    ),
    (
        "report_issue",
        (
            "report an issue",
            "report issue",
            "report the data",
            "file a bug",
            "report a bug",
            "report this",
            "data issue",
        ),
    ),
    (
        "data_clean",
        (
            "data clean",
            "data quality",
            "is the data",
            "clean the data",
            "data problems",
            "freshness",
            "duplicates",
            "null",
            "dq",
        ),
    ),
    ("churn_why", ("churn", "cancel", "retention", "spike", "why did", "downgrade")),
    (
        "q3_sales",
        ("q3", "q2", "quarter", "sales", "revenue", "arr", "mrr", "growth", "trend", "book of business"),
    ),
)


@dataclass(frozen=True)
class Question:
    """One typed decision question (Choice / Noul / Score per PRD §7)."""

    id: str
    type: str  # "choice" | "noul" | "score"
    prompt: str
    options: tuple[str, ...] = field(default=())


def classify_route(text: str) -> RouteId:
    """Map free text to a route with the ordered keyword table (deterministic)."""
    haystack = " " + re.sub(r"[^a-z0-9 ]+", " ", text.lower()) + " "
    for route, keywords in _ROUTE_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return route
    return "off_catalog"


NOUL_DATA_PROMPT = "Is this movement a real business change or a data problem?"
NOUL_ANOMALY_PROMPT = "Is there an anomaly worth a callout?"
SCORE_DQ_PROMPT = "Data-quality confidence 0–1"
GRANULARITY_PROMPT = "At what granularity should the answer land?"


def build_questions(route: RouteId) -> list[Question]:
    """The five-question set per query (component granularity per route)."""
    granularities = {
        "q3_sales": ("month", "week", "quarter", "account"),
        "churn_why": ("account", "week", "month", "quarter"),
        "data_clean": ("check", "event", "segment", "day"),
        "investigate_churn": ("account", "day", "week", "segment"),
        "report_issue": ("summary", "check", "event", "account"),
        "off_catalog": ("summary", "detailed", "list", "table"),
    }
    component_prompt = "Which catalog components does this query need?"
    return [
        Question("component_choice", "choice", component_prompt, CATALOG_COMPONENTS),
        Question("real_vs_data_problem", "noul", NOUL_DATA_PROMPT,
                 ("data_problem", "real_change")),
        Question("anomaly_callout", "noul", NOUL_ANOMALY_PROMPT,
                 ("anomaly", "no_anomaly")),
        Question("dq_confidence", "score", SCORE_DQ_PROMPT),
        Question("granularity", "choice", GRANULARITY_PROMPT, granularities[route]),
    ]

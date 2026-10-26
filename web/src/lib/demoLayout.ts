// Issue #12: the demo canvas fixture — one A2UI layout (PRD §10 envelope,
// valid per parseA2UILayout) carrying all six catalog types. The canvas is
// fixture-driven until the chat issues (#33/#36) land; the shape mirrors what
// agent/catalog.py would emit for the README's planted Q3-2026 story, and
// `agent/quality.py`'s _CHECK_NAMES order (freshness, duplicates, nulls).
// Table rows are generated, not hand-listed — the schema caps data_table at
// 50 rows (PRD §7).

import type { QualityCheck } from "../components/catalog/DataQualityCard";

// The acceptance KPI is the first tile, verbatim.
const DEMO_KPIS = [
  { label: "Net-new ARR", value: 1_200_000, delta: 0.18 },
  { label: "MRR", value: 98_500, delta: 0.04 },
  { label: "Active accounts", value: 1_240, delta: 0.02 },
  { label: "Gross churn", value: 42_000, delta: -0.03 },
];

const DEMO_SEGMENTS = ["Enterprise", "Growth", "Startup"];
const ACCOUNT_STEMS = [
  "Acme",
  "Northwind",
  "Globex",
  "Initech",
  "Umbrella",
  "Stark",
  "Wayne",
  "Hooli",
  "Pied Piper",
  "Soylent",
];
const ACCOUNT_SUFFIXES = ["Corp", "LLC", "Inc", "Group", "Labs"];

function demoTableRows(): Record<string, unknown>[] {
  return Array.from({ length: 50 }, (_, index) => ({
    account: `${ACCOUNT_STEMS[index % 10]} ${ACCOUNT_SUFFIXES[Math.floor(index / 10) % 5]} #${100 + index}`,
    segment: DEMO_SEGMENTS[index % 3],
    mrr: 1_200 + ((index * 137) % 8_800),
  }));
}

// The three quality checks in quality.py's _CHECK_NAMES order, banded by
// run_checks' thresholds: a duplicates fail, a nulls warn, freshness ok.
const DEMO_CHECKS: QualityCheck[] = [
  { name: "freshness", status: "ok", score: 0.97 },
  { name: "duplicates", status: "fail", score: 0.81 },
  { name: "nulls", status: "warn", score: 0.93 },
];

export const DEMO_LAYOUT = {
  v: 1 as const,
  layout: "grid" as const,
  items: [
    { type: "kpi_row", props: { kpis: DEMO_KPIS } },
    {
      type: "line_chart",
      props: {
        series: "arr_daily",
        range: "2026-07-01..2026-09-30",
        annotations: [
          "Aug 10 — pricing-change churn begins",
          "Sep 8–21 — ingest bug duplicates usage rows",
        ],
      },
    },
    {
      type: "variance_callout",
      props: {
        metric: "MRR churn",
        delta: 0.42,
        baseline: "the trailing 90 days",
        cause:
          "The Aug 10 pricing change pushed ~40 Growth-segment accounts onto a new plan and cancellation ran through Sep 30.",
        cta: "churn cohort",
      },
    },
    {
      type: "data_table",
      props: {
        columns: ["account", "segment", "mrr"],
        rows: demoTableRows(),
        filters: ["segment"],
      },
    },
    { type: "data_quality_card", props: { checks: DEMO_CHECKS } },
    {
      type: "insight_text",
      props: {
        title: "Q3 took off — with one dent",
        body:
          "Net-new ARR compounded through Q3 to $2.7M, driven by the Enterprise ramp. The Aug 10 pricing change bent the curve for six weeks; churn normalized after Sep 30. Data quality needs a look before this goes in the board deck — duplicates are failing and nulls warn.",
      },
    },
  ],
  slotCopy: {
    "kpis.title": "Q3-2026 at a glance",
    "line_chart.title": "Daily ARR through Q3",
    "narrative.body":
      "Sources: subscription_events, revenue_events (2026-07-01..2026-09-30).",
  },
};

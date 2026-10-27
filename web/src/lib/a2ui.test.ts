// Acceptance-contract tests for the frontend A2UI Zod schema (issue #11).
//
// Pinned contract: a2ui.ts mirrors agent/catalog.py — the same six component
// types in the same order, the same prop field names, extra="forbid" as
// .strict(), and the PRD §10 wire envelope {v: 1, layout: "grid", items,
// slotCopy}. Two deliberate divergences from the server schema, both required
// by issue #11: the item envelope is permissive (unknown types survive to the
// renderer and degrade per item) and kpis has no 3–4 length bound (the PRD
// §10 example carries a single KPI and must parse).

import { describe, expect, it } from "vitest";
import {
  COMPONENT_TYPES,
  a2uiFromState,
  componentPropsSchemas,
  parseA2UILayout,
  validateComponentProps,
} from "./a2ui";

// Verbatim the PRD §10 wire example (PRD.md §10, "A2UI layout").
const PRD_SECTION_10_LAYOUT = {
  v: 1,
  layout: "grid",
  items: [
    {
      type: "kpi_row",
      props: {
        kpis: [{ label: "Net-new ARR", value: 1200000, delta: 0.18 }],
      },
    },
    {
      type: "line_chart",
      props: { series: "arr_daily", range: "2026-07-01..2026-09-30" },
    },
    {
      type: "variance_callout",
      props: {
        metric: "MRR churn",
        delta: 0.42,
        cause: "pricing change Aug 10",
        cta: "investigate_churn_cohort",
      },
    },
  ],
  slotCopy: { "kpis.title": "Q3 revenue took off" },
};

describe("parseA2UILayout (acceptance: PRD §10 layout parses)", () => {
  it("parses the PRD §10 wire example with data matching field-for-field", () => {
    const result = parseA2UILayout(PRD_SECTION_10_LAYOUT);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.v).toBe(1);
    expect(result.data.layout).toBe("grid");
    expect(result.data.items).toHaveLength(3);

    const [kpiRow, lineChart, callout] = result.data.items;
    expect(kpiRow.type).toBe("kpi_row");
    expect(kpiRow.props).toEqual({
      kpis: [{ label: "Net-new ARR", value: 1200000, delta: 0.18 }],
    });

    expect(lineChart.type).toBe("line_chart");
    // Field-for-field with the payload: the permissive envelope keeps props
    // exactly as sent; the annotations [] default is per-type behavior,
    // asserted against validateComponentProps below.
    expect(lineChart.props).toEqual({
      series: "arr_daily",
      range: "2026-07-01..2026-09-30",
    });

    expect(callout.type).toBe("variance_callout");
    expect(callout.props).toEqual({
      metric: "MRR churn",
      delta: 0.42,
      baseline: undefined, // optional str | None, absent in the §10 example
      cause: "pricing change Aug 10",
      cta: "investigate_churn_cohort",
    });

    expect(result.data.slotCopy).toEqual({ "kpis.title": "Q3 revenue took off" });
  });

  it("defaults slotCopy to {} when absent, mirroring catalog.A2UILayout", () => {
    const result = parseA2UILayout({ v: 1, layout: "grid", items: [] });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.slotCopy).toEqual({});
  });
});

describe("parseA2UILayout (envelope rejections)", () => {
  const cases: [string, unknown][] = [
    ["v other than literal 1", { v: 2, layout: "grid", items: [] }],
    ["missing items", { v: 1, layout: "grid" }],
    ["items not an array", { v: 1, layout: "grid", items: "nope" }],
    ["slotCopy with a non-string value", { ...PRD_SECTION_10_LAYOUT, slotCopy: { "kpis.title": 42 } }],
    ["unknown top-level key", { ...PRD_SECTION_10_LAYOUT, extra: true }],
  ];

  it.each(cases)("%s → {ok: false} with a non-empty error", (_label, payload) => {
    const result = parseA2UILayout(payload);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.length).toBeGreaterThan(0);
  });
});

describe("catalog contract (acceptance: types and prop names match agent/catalog.py)", () => {
  it("pins the six component types in the server union order", () => {
    expect(COMPONENT_TYPES).toEqual([
      "kpi_row",
      "line_chart",
      "variance_callout",
      "data_table",
      "data_quality_card",
      "insight_text",
    ]);
  });

  it("keys componentPropsSchemas by exactly the same types", () => {
    expect(Object.keys(componentPropsSchemas)).toEqual([
      "kpi_row",
      "line_chart",
      "variance_callout",
      "data_table",
      "data_quality_card",
      "insight_text",
    ]);
  });
});

describe("a2uiFromState (issue #17: tolerant state extraction)", () => {
  const layout = { v: 1, layout: "grid", items: [], slotCopy: {} };

  it("returns the parsed layout from a state carrying a2ui", () => {
    expect(a2uiFromState({ a2ui: layout, decisions: [], status: "done" })).toEqual(
      layout,
    );
  });

  it("returns null when a2ui is absent (own-property check only)", () => {
    expect(a2uiFromState({ decisions: [] })).toBeNull();
    // Inherited Object.prototype members must not read as an a2ui payload.
    expect(a2uiFromState(Object.create({ a2ui: layout }))).toBeNull();
  });

  it("returns null for an invalid a2ui payload", () => {
    expect(a2uiFromState({ a2ui: { v: 2, layout: "grid", items: [] } })).toBeNull();
    expect(a2uiFromState({ a2ui: "garbage" })).toBeNull();
  });

  it("returns null for non-object states", () => {
    expect(a2uiFromState(null)).toBeNull();
    expect(a2uiFromState(undefined)).toBeNull();
    expect(a2uiFromState("state")).toBeNull();
    expect(a2uiFromState(["a2ui"])).toBeNull();
  });
});

describe("per-type prop schemas (prop names verbatim from catalog.py)", () => {
  it("kpi_row parses {kpis: [{label, value, delta}]} — unbounded length (PRD §10 sample has one KPI)", () => {
    expect(
      validateComponentProps("kpi_row", {
        kpis: [{ label: "Net-new ARR", value: 1200000, delta: 0.18 }],
      }),
    ).toMatchObject({ ok: true });
  });

  it("line_chart parses {series, range} with annotations defaulting to []", () => {
    const result = validateComponentProps("line_chart", {
      series: "arr_daily",
      range: "2026-07-01..2026-09-30",
    });
    expect(result).toEqual({
      ok: true,
      data: { series: "arr_daily", range: "2026-07-01..2026-09-30", annotations: [] },
    });
  });

  it("line_chart accepts optional annotations list[str]", () => {
    expect(
      validateComponentProps("line_chart", {
        series: "arr_daily",
        range: "2026-07-01..2026-09-30",
        annotations: ["Aug 1 campaign"],
      }),
    ).toMatchObject({ ok: true });
  });

  it("variance_callout parses {metric, delta} and the optional baseline/cause/cta slots", () => {
    expect(
      validateComponentProps("variance_callout", { metric: "MRR churn", delta: 0.42 }),
    ).toMatchObject({ ok: true });
    expect(
      validateComponentProps("variance_callout", {
        metric: "MRR churn",
        delta: 0.42,
        baseline: null, // str | None server-side
        cause: "pricing change Aug 10",
        cta: "investigate_churn_cohort",
      }),
    ).toMatchObject({ ok: true });
  });

  it("data_table parses {columns, rows} and caps rows at 50 (PRD §7 rows ≤50)", () => {
    expect(
      validateComponentProps("data_table", {
        columns: ["account", "mrr"],
        rows: [{ account: "Acme", mrr: 999 }, { account: "Globex", mrr: 1234 }],
      }),
    ).toMatchObject({ ok: true });

    const fifty = Array.from({ length: 50 }, (_, i) => ({ account: `a${i}`, mrr: i }));
    expect(
      validateComponentProps("data_table", { columns: ["account"], rows: fifty }),
    ).toMatchObject({ ok: true });

    const fiftyOne = Array.from({ length: 51 }, (_, i) => ({ account: `a${i}`, mrr: i }));
    expect(
      validateComponentProps("data_table", { columns: ["account"], rows: fiftyOne }),
    ).toMatchObject({ ok: false });
  });

  it("data_quality_card mirrors quality.py's {name, status, score} rows: ok|warn|fail, score 0..1", () => {
    expect(
      validateComponentProps("data_quality_card", {
        checks: [{ name: "freshness", status: "ok", score: 1 }],
      }),
    ).toMatchObject({ ok: true });

    expect(
      validateComponentProps("data_quality_card", {
        checks: [{ name: "duplicates", status: "error", score: 0.5 }],
      }),
    ).toMatchObject({ ok: false });

    expect(
      validateComponentProps("data_quality_card", {
        checks: [{ name: "nulls", status: "warn", score: 1.5 }],
      }),
    ).toMatchObject({ ok: false });

    expect(
      validateComponentProps("data_quality_card", {
        checks: [{ name: "nulls", status: "fail", score: -0.1 }],
      }),
    ).toMatchObject({ ok: false });
  });

  it("insight_text parses {title, body}", () => {
    expect(
      validateComponentProps("insight_text", {
        title: "Nothing to visualize",
        body: "No dashboard component scored high enough.",
      }),
    ).toMatchObject({ ok: true });
  });

  it.each([
    ["kpi_row", "kpis", { }],
    ["line_chart", "series", { range: "2026-07-01..2026-09-30" }],
    ["line_chart", "range", { series: "arr_daily" }],
    ["variance_callout", "delta", { metric: "MRR churn" }],
    ["data_table", "rows", { columns: ["account"] }],
    ["data_table", "columns", { rows: [{ account: "Acme" }] }],
    ["data_quality_card", "checks", { }],
    ["insight_text", "body", { title: "Nothing to visualize" }],
  ] as [string, string, unknown][])(
    "%s without required prop %s fails closed",
    (type, _required, partial) => {
      expect(validateComponentProps(type, partial)).toMatchObject({ ok: false });
    },
  );

  it("mirrors catalog.py extra=\"forbid\": an unknown props key fails", () => {
    expect(
      validateComponentProps("line_chart", {
        series: "arr_daily",
        range: "2026-07-01..2026-09-30",
        seriess: "typo",
      }),
    ).toMatchObject({ ok: false });
  });

  it("fails closed for types the catalog does not know", () => {
    expect(validateComponentProps("bogus_chart", {})).toMatchObject({ ok: false });
  });

  it("fails closed without throwing for prototype-chain type names", () => {
    // Regression: the schema map is a plain object, so its inherited members
    // (toString, constructor, ...) used to resolve as "schemas" and throw
    // schema.safeParse is not a function instead of returning {ok: false}.
    for (const type of [
      "toString",
      "valueOf",
      "hasOwnProperty",
      "constructor",
      "__proto__",
    ]) {
      expect(validateComponentProps(type, {})).toMatchObject({ ok: false });
    }
  });
});

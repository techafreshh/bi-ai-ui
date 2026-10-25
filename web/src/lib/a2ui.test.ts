/** Zod layout validation: valid / invalid / unknown types / state extraction. */
import { describe, expect, it } from "vitest";
import {
  A2UI_ITEM_TYPES,
  extractAgentUiState,
  validateLayout,
} from "./a2ui";

const validLayout = {
  v: 1,
  layout: "grid",
  route: "q3_sales",
  items: [
    { type: "kpi_row", props: { kpis: [{ label: "x", value: 1, delta: 0.1 }] } },
    { type: "line_chart", props: { series: "arr_daily", range: "r", points: [{ day: "d" }] } },
  ],
  slotCopy: { "insight_text.body": "copy" },
};

describe("validateLayout", () => {
  it("accepts a well-formed layout", () => {
    const result = validateLayout(validLayout);
    expect(result.layout).toBeDefined();
    expect(result.layout?.items).toHaveLength(2);
    expect(result.error).toBeUndefined();
  });

  it("rejects non-object payloads", () => {
    expect(validateLayout("nope").fallback).toBe(true);
    expect(validateLayout(42).fallback).toBe(true);
    expect(validateLayout(null).fallback).toBe(true);
    expect(validateLayout(undefined).fallback).toBe(true);
  });

  it("rejects layouts without items", () => {
    const result = validateLayout({ v: 1, layout: "grid", route: "q3_sales" });
    expect(result.layout).toBeUndefined();
    expect(result.fallback).toBe(true);
    expect(result.error).toBeTruthy();
  });

  it("rejects items without a props object", () => {
    const result = validateLayout({ ...validLayout, items: [{ type: "kpi_row" }] });
    // zod record default kicks in: missing props becomes {} — still valid.
    expect(result.layout?.items[0].props).toEqual({});
  });

  it("rejects malformed slotCopy", () => {
    const result = validateLayout({ ...validLayout, slotCopy: { a: 3 } });
    expect(result.layout).toBeUndefined();
  });
});

describe("unknown component types", () => {
  it("stay parseable so the registry can fall back per item", () => {
    // The schema keeps type: string by design — unknown members must not
    // poison the whole grid (PRD: fallback card, never an error).
    const result = validateLayout({
      ...validLayout,
      items: [{ type: "dragon_chart", props: {} }],
    });
    expect(result.layout?.items[0].type).toBe("dragon_chart");
    expect(A2UI_ITEM_TYPES).not.toContain("dragon_chart");
  });
});

describe("extractAgentUiState", () => {
  it("tracks keys independently across partial snapshots", () => {
    // Snapshots REPLACE the state object: a snapshot without decisions must
    // not invent an empty list where the frontend expects the last known one.
    const onlyStatus = extractAgentUiState({ status: { stage: "deciding" } });
    expect(onlyStatus.decisions).toEqual([]);
    expect(onlyStatus.status.stage).toBe("deciding");
  });

  it("drops malformed decision records", () => {
    const state = extractAgentUiState({
      decisions: [
        { id: "ok", model: "m", latency_ms: 1, probabilities: {}, question: "q", options: [] },
        "garbage",
        null,
        { nope: true },
      ],
      a2ui: validLayout,
    });
    expect(state.decisions).toHaveLength(1);
    expect(state.a2ui?.items).toHaveLength(2);
  });

  it("treats null a2ui as no layout", () => {
    expect(extractAgentUiState({ a2ui: null }).a2ui).toBeNull();
  });
});

/** Registry: unknown catalog types always land on FallbackCard. */
import { describe, expect, it } from "vitest";
import { CATALOG, componentFor, isKnownType } from "../components/catalog/registry";

describe("registry", () => {
  it("maps all six catalog components", () => {
    expect(Object.keys(CATALOG).sort()).toEqual(
      [
        "data_quality_card",
        "data_table",
        "insight_text",
        "kpi_row",
        "line_chart",
        "variance_callout",
      ].sort(),
    );
  });

  it("maps unknown types to the fallback card", () => {
    expect(componentFor("dragon_chart")).toBe(componentFor("___never_registered___"));
    expect(componentFor("kpi_row")).not.toBe(componentFor("dragon_chart"));
  });

  it("isKnownType discriminates catalog members", () => {
    expect(isKnownType({ type: "kpi_row", props: {} })).toBe(true);
    expect(isKnownType({ type: "dragon_chart", props: {} })).toBe(false);
  });
});

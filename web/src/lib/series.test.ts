import { describe, expect, it } from "vitest";
import { buildSeries, parseRange } from "./series";

describe("parseRange", () => {
  it("parses the a..b inclusive date range", () => {
    expect(parseRange("2026-07-01..2026-09-30")).toEqual({
      start: "2026-07-01",
      end: "2026-09-30",
    });
  });

  it("rejects garbage, impossible dates and inverted bounds", () => {
    expect(parseRange("not a range")).toBeNull();
    expect(parseRange("2026-02-31..2026-03-31")).toBeNull();
    expect(parseRange("2026-09-30..2026-07-01")).toBeNull();
  });
});

describe("buildSeries", () => {
  it("yields 92 inclusive daily points for the Q3-2026 range", () => {
    // Jul has 31 + Aug 31 + Sep 30 = 92 days, both bounds included.
    const points = buildSeries("arr_daily", "2026-07-01..2026-09-30");
    expect(points).toHaveLength(92);
    expect(points[0].date).toBe("2026-07-01");
    expect(points[91].date).toBe("2026-09-30");
  });

  it("is deterministic and grows through Q3 despite the Aug-10 churn kink", () => {
    const a = buildSeries("arr_daily", "2026-07-01..2026-07-02");
    const b = buildSeries("arr_daily", "2026-07-01..2026-07-02");
    expect(a).toEqual(b);
    const q3 = buildSeries("arr_daily", "2026-07-01..2026-09-30");
    expect(q3[0].value).toBeLessThan(q3[91].value);
    expect(q3[0].value).toBeGreaterThan(0);
    // Values are whole dollars.
    expect(Number.isInteger(q3[41].value)).toBe(true);
  });

  it("returns no points for an unknown series name", () => {
    expect(buildSeries("mystery_daily", "2026-07-01..2026-09-30")).toEqual([]);
  });

  it("returns no points for an unparseable range", () => {
    expect(buildSeries("arr_daily", "yesterday")).toEqual([]);
  });
});

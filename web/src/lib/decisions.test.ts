// Behavior tests for the decisions wire-mirror helpers (issue #14).
//
// Pinned contract: extraction from a state snapshot is tolerant (garbage
// state, inherited keys, malformed entries -> [] / skipped, never thrown, and
// prototype-chain `decisions` members are rejected by the own-property-only
// lookup); a record missing latency_ms is NOT dropped (it must reach the
// drawer to render "n/a"); scalar vs dict probabilities produce labeled rows;
// latency formatting renders 0 as "0 ms" and absent as "n/a".

import { describe, expect, it } from "vitest";
import {
  barWidthPercent,
  decisionsFromState,
  formatLatency,
  formatPercent,
  recordProbabilities,
  sameDecisions,
  type DecisionRecord,
} from "./decisions";

const VALID: DecisionRecord[] = [
  {
    id: "catalog_components",
    type: "choice",
    probabilities: { kpi_row: 0.9, line_chart: 0.85 },
    pick: "kpi_row",
    model: "~perplexity/pplx-decider-flash-latest",
    latency_ms: 232.41,
  },
  {
    id: "real_change_vs_data_problem",
    type: "noul",
    probabilities: 0.9,
    pick: "yes",
  },
];

describe("decisionsFromState (extraction)", () => {
  it("extracts the decisions array from a state snapshot verbatim", () => {
    const state = { decisions: VALID, status: "decide" };
    expect(decisionsFromState(state)).toEqual(VALID);
  });

  it("returns [] for null, primitives, and arrays", () => {
    expect(decisionsFromState(null)).toEqual([]);
    expect(decisionsFromState(42)).toEqual([]);
    expect(decisionsFromState("nope")).toEqual([]);
    expect(decisionsFromState([VALID[0]])).toEqual([]);
  });

  it("returns [] when the decisions key is missing or not an array", () => {
    expect(decisionsFromState({})).toEqual([]);
    expect(decisionsFromState({ decisions: 3 })).toEqual([]);
    expect(decisionsFromState({ decisions: { id: "x" } })).toEqual([]);
  });

  it("ignores a decisions key inherited from Object.prototype", () => {
    const hostile = Object.create({ decisions: VALID }) as object;
    expect(decisionsFromState(hostile)).toEqual([]);
  });

  it("filters out garbage entries and keeps valid siblings", () => {
    const state = {
      decisions: [
        null,
        42,
        "catalog_components",
        {},
        { type: "choice", probabilities: 1 }, // no id
        { id: "", type: "choice" }, // empty id
        { id: "x" }, // no type
        { id: "x", type: 7 }, // non-string type
        VALID[0],
      ],
    };
    expect(decisionsFromState(state)).toEqual([VALID[0]]);
  });
});

describe("recordProbabilities (scalar and dict)", () => {
  it("labels a scalar noul probability 'yes'", () => {
    expect(recordProbabilities(VALID[1])).toEqual([{ label: "yes", value: 0.9 }]);
  });

  it("labels a scalar score probability 'score'", () => {
    expect(recordProbabilities({ id: "s", type: "score", probabilities: 0.82 })).toEqual([
      { label: "score", value: 0.82 },
    ]);
  });

  it("labels a scalar probability of any other type 'p'", () => {
    expect(recordProbabilities({ id: "c", type: "choice", probabilities: 0.5 })).toEqual([
      { label: "p", value: 0.5 },
    ]);
  });

  it("yields one row per dict option with a finite value, keeping zeros", () => {
    const record: DecisionRecord = {
      id: "c",
      type: "choice",
      probabilities: { kpi_row: 0.9, line_chart: 0, data_table: Number.NaN },
    };
    expect(recordProbabilities(record)).toEqual([
      { label: "kpi_row", value: 0.9 },
      { label: "line_chart", value: 0 },
    ]);
  });

  it("yields no rows for missing or garbage probabilities", () => {
    expect(
      recordProbabilities({ id: "x", type: "choice", probabilities: "junk" as unknown as number }),
    ).toEqual([]);
    expect(
      recordProbabilities({ id: "x", type: "choice", probabilities: Number.NaN }),
    ).toEqual([]);
  });
});

describe("sameDecisions (dedupe)", () => {
  it("is true for the same array order and content", () => {
    expect(sameDecisions(VALID, [...VALID])).toBe(true);
  });

  it("is false for different content or a different order", () => {
    expect(sameDecisions(VALID, [VALID[0]])).toBe(false);
    expect(sameDecisions([VALID[0], VALID[1]], [VALID[1], VALID[0]])).toBe(false);
  });

  it("is false against an empty array", () => {
    expect(sameDecisions(VALID, [])).toBe(false);
  });
});

describe("formats (percent, latency, bar width)", () => {
  it("formats a probability as an integer-rounded percent string", () => {
    expect(formatPercent(0.91)).toBe("91%");
    expect(formatPercent(0.902)).toBe("90%");
    expect(formatPercent(0)).toBe("0%");
  });

  it("rounds latency down to whole milliseconds with a unit", () => {
    expect(formatLatency(123.45)).toBe("123 ms");
  });

  it("renders an absent latency as n/a, never crashing", () => {
    expect(formatLatency(undefined)).toBe("n/a");
    expect(formatLatency(null)).toBe("n/a");
    expect(formatLatency(Number.NaN)).toBe("n/a");
  });

  it("keeps a zero latency as a real value", () => {
    expect(formatLatency(0)).toBe("0 ms");
  });

  it("clamps bar widths into [0%, 100%]", () => {
    expect(barWidthPercent(0.902)).toBe("90%");
    expect(barWidthPercent(0)).toBe("0%");
    expect(barWidthPercent(1)).toBe("100%");
    expect(barWidthPercent(1.5)).toBe("100%");
    expect(barWidthPercent(-0.2)).toBe("0%");
  });
});

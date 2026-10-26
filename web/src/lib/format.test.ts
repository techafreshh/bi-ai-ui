import { describe, expect, it } from "vitest";
import { formatCurrency, formatDelta, formatScore, humanizeKey } from "./format";

describe("formatCurrency", () => {
  it("renders millions with two decimals and the $M suffix", () => {
    expect(formatCurrency(1_200_000)).toBe("$1.20M");
    expect(formatCurrency(2_734_567)).toBe("$2.73M");
  });

  it("renders thousands with one decimal and the $K suffix", () => {
    expect(formatCurrency(24_500)).toBe("$24.5K");
  });

  it("renders sub-1K values as full dollars", () => {
    expect(formatCurrency(999)).toBe("$999");
    expect(formatCurrency(512)).toBe("$512");
  });

  it("keeps the sign in front of the dollar sign for negatives", () => {
    expect(formatCurrency(-50_000)).toBe("-$50.0K");
  });
});

describe("formatDelta", () => {
  it("prefixes gains with + and percents as whole numbers", () => {
    expect(formatDelta(0.18)).toBe("+18%");
    expect(formatDelta(0.42)).toBe("+42%");
  });

  it("renders losses with the minus sign and no plus", () => {
    expect(formatDelta(-0.03)).toBe("-3%");
  });

  it("renders zero without a sign", () => {
    expect(formatDelta(0)).toBe("0%");
  });
});

describe("formatScore", () => {
  it("renders a [0,1] confidence as a whole percent", () => {
    expect(formatScore(0.95)).toBe("95%");
    expect(formatScore(1)).toBe("100%");
    expect(formatScore(0)).toBe("0%");
  });
});

describe("humanizeKey", () => {
  it("title-cases snake_case keys", () => {
    expect(humanizeKey("account")).toBe("Account");
    expect(humanizeKey("null_scope")).toBe("Null Scope");
  });

  it("keeps known domain acronyms uppercase", () => {
    expect(humanizeKey("mrr")).toBe("MRR");
    expect(humanizeKey("net_new_arr")).toBe("Net New ARR");
  });

  it("returns the empty string for separators only", () => {
    expect(humanizeKey("_-")).toBe("");
  });
});

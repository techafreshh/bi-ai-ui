// Acceptance-contract tests for the DecisionDrawer (issue #14, simplified
// row in issue #87).
//
// Pinned contract: the header "Decision log" button toggles a right-side
// drawer (data-testid="decision-drawer") whose close control returns to the
// closed state; a completed Q3 query renders >=2 decision entries, each as
// one flat text row containing the literal substrings "p=", "model", and
// "latency" (scalar probabilities render as "p=<scalar>"); a record missing
// latency_ms renders "n/a" without crashing; the empty drawer renders a
// "no dataset" note; the union of two queries' decisions renders one entry
// per streamed record — count equals the sum of both array lengths.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DecisionDrawer } from "./DecisionDrawer";
import type { DecisionRecord } from "../lib/decisions";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// A completed Q3 query's decisions, shaped like agent/graph.py decide()
// stamps them: one choice record with a full option->probability dict, one
// noul scalar record with NO latency_ms (the n/a acceptance arm).
const Q3_DECISIONS: DecisionRecord[] = [
  {
    id: "catalog_components",
    type: "choice",
    probabilities: {
      kpi_row: 0.9,
      line_chart: 0.85,
      variance_callout: 0,
      data_table: 0,
      data_quality_card: 0,
      insight_text: 0,
    },
    pick: "kpi_row",
    model: "~perplexity/pplx-decider-flash-latest",
    latency_ms: 232.41,
  },
  {
    id: "real_change_vs_data_problem",
    type: "noul",
    probabilities: 0.9,
    pick: "yes",
    model: "~perplexity/pplx-decider-flash-latest",
  },
];

// A second query on the same session: its decide exit broadcasts a fresh
// array (different ids and latencies), which must accumulate into the union.
const SECOND_QUERY: DecisionRecord[] = [
  {
    id: "metric_focus",
    type: "choice",
    probabilities: {
      kpi_row: 0,
      line_chart: 1,
      variance_callout: 0,
      data_table: 0,
      data_quality_card: 0,
      insight_text: 0,
    },
    pick: "line_chart",
    model: "~perplexity/pplx-decider-flash-latest",
    latency_ms: 87.2,
  },
  {
    id: "needs_drilldown",
    type: "score",
    probabilities: 0.82,
    pick: 0.82,
    model: "~perplexity/pplx-decider-flash-latest",
    latency_ms: 41,
  },
];

describe("DecisionDrawer (acceptance: open/close cycle)", () => {
  it("starts closed, opens on the header button, closes on the close control", () => {
    render(<DecisionDrawer decisions={Q3_DECISIONS} />);
    expect(screen.queryByTestId("decision-drawer")).toBeNull();
    fireEvent.click(screen.getByTestId("decision-log-button"));
    expect(screen.getByTestId("decision-drawer")).toBeTruthy();
    fireEvent.click(screen.getByTestId("decision-drawer-close"));
    expect(screen.queryByTestId("decision-drawer")).toBeNull();
  });

  it("reflects the open state on the toggle and names the dialog", () => {
    render(<DecisionDrawer decisions={Q3_DECISIONS} />);
    const toggle = screen.getByTestId("decision-log-button");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    const drawer = screen.getByTestId("decision-drawer");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(drawer.getAttribute("role")).toBe("dialog");
    expect(drawer.getAttribute("aria-label")).toBe("Decision log");
  });

  it("shows the no dataset note before any decision has streamed", () => {
    render(<DecisionDrawer decisions={[]} />);
    fireEvent.click(screen.getByTestId("decision-log-button"));
    expect(screen.getByText(/no dataset/)).toBeTruthy();
    expect(console.error).not.toHaveBeenCalled();
  });
});

describe("DecisionDrawer (acceptance: flat p=/model/latency row)", () => {
  it("renders >=2 decision entries whose text contains p=, model, and latency", () => {
    render(<DecisionDrawer decisions={Q3_DECISIONS} />);
    fireEvent.click(screen.getByTestId("decision-log-button"));

    const entries = screen.getAllByTestId("decision-entry");
    expect(entries.length).toBeGreaterThanOrEqual(2);
    for (const entry of entries) {
      const text = entry.textContent ?? "";
      expect(text).toContain("p=");
      expect(text).toContain("model");
      expect(text).toContain("latency");
    }
    // Model attribution passes through verbatim and keeps the pplx marker.
    for (const model of screen.getAllByTestId("decision-model")) {
      expect(model.textContent).toContain("pplx");
    }
    // Latency strings match /\\d+ ms/.
    expect(
      screen
        .getAllByTestId("decision-latency")
        .some((el) => /\d+ ms/.test(el.textContent ?? "")),
    ).toBe(true);
    expect(screen.getByText("232 ms")).toBeTruthy();
    // The picked option rounds the entry out.
    expect(screen.getByText("picked: kpi_row")).toBeTruthy();
    expect(console.error).not.toHaveBeenCalled();
  });

  it("renders a scalar probability as p=<scalar>", () => {
    render(<DecisionDrawer decisions={Q3_DECISIONS} />);
    fireEvent.click(screen.getByTestId("decision-log-button"));
    const entries = screen.getAllByTestId("decision-entry");
    // The second entry is the noul scalar 0.9.
    expect(entries[1].textContent).toContain("p=0.9");
    expect(console.error).not.toHaveBeenCalled();
  });

  it("renders the record missing latency_ms as n/a instead of crashing", () => {
    render(<DecisionDrawer decisions={Q3_DECISIONS} />);
    fireEvent.click(screen.getByTestId("decision-log-button"));
    const latencies = screen.getAllByTestId("decision-latency");
    expect(latencies.some((el) => el.textContent === "n/a")).toBe(true);
    expect(latencies.some((el) => el.textContent === "232 ms")).toBe(true);
    expect(console.error).not.toHaveBeenCalled();
  });
});

describe("DecisionDrawer (acceptance: union across two queries)", () => {
  it("accumulates both queries' decisions — count equals the sum of the arrays", () => {
    const view = render(<DecisionDrawer decisions={Q3_DECISIONS} />);
    fireEvent.click(screen.getByTestId("decision-log-button"));
    expect(screen.getAllByTestId("decision-entry")).toHaveLength(
      Q3_DECISIONS.length,
    );

    view.rerender(
      <DecisionDrawer decisions={[...Q3_DECISIONS, ...SECOND_QUERY]} />,
    );
    const entries = screen.getAllByTestId("decision-entry");
    expect(entries).toHaveLength(Q3_DECISIONS.length + SECOND_QUERY.length);
    // Both runs' records are present, duplicates included, each with the
    // flat-row literals.
    expect(screen.getByText("#catalog_components")).toBeTruthy();
    expect(screen.getByText("#metric_focus")).toBeTruthy();
    for (const entry of entries) {
      expect(entry.textContent).toContain("p=");
      expect(entry.textContent).toContain("model");
      expect(entry.textContent).toContain("latency");
    }
    expect(screen.getByText("41 ms")).toBeTruthy();
    expect(console.error).not.toHaveBeenCalled();
  });
});

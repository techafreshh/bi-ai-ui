// Acceptance-contract tests for A2UIRenderer (issue #11).
//
// Pinned contract: {"type":"bogus_chart","props":{}} mounts as an
// "Unknown component: bogus_chart" fallback card with exactly one
// console.error; a layout failing Zod validation renders a single
// "Invalid layout" card without crashing; known types with valid props
// render their catalog component silently. Every fallback path emits
// exactly one decision-log-style console.error.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { A2UIRenderer } from "./A2UIRenderer";
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// The PRD §10 kpi_row item, verbatim.
const KPI_ROW_ITEM = {
  type: "kpi_row",
  props: { kpis: [{ label: "Net-new ARR", value: 1200000, delta: 0.18 }] },
};

describe("A2UIRenderer (acceptance: unknown type degrades, never errors)", () => {
  it('renders "Unknown component: bogus_chart" and calls console.error exactly once', () => {
    render(<A2UIRenderer items={[{ type: "bogus_chart", props: {} }]} />);
    expect(screen.getByText("Unknown component: bogus_chart")).toBeTruthy();
    expect(screen.getByTestId("a2ui-fallback")).toBeTruthy();
    expect(console.error).toHaveBeenCalledTimes(1);
  });

  it('logs the decision in the "[a2ui] … — rendered fallback card" format', () => {
    render(<A2UIRenderer items={[{ type: "bogus_chart", props: {} }]} />);
    expect(console.error).toHaveBeenCalledWith(
      '[a2ui] unknown component type "bogus_chart" — rendered fallback card',
    );
  });

  it("renders a known type as its real catalog component, silently", () => {
    render(<A2UIRenderer items={[KPI_ROW_ITEM]} />);
    const root = screen.getByTestId("catalog-kpi_row");
    // The acceptance KPI, formatted by the real component (issue #12).
    expect(root.textContent).toContain("Net-new ARR");
    expect(root.textContent).toContain("$1.20M");
    expect(root.textContent).toContain("+18%");
    expect(console.error).not.toHaveBeenCalled();
  });

  it("renders the fallback card for type strings naming Object.prototype members, without crashing", () => {
    // Regression (review finding): the registry/schema lookups are plain
    // objects, so "toString" & co. used to resolve truthy and crash the
    // render with schema.safeParse is not a function — exactly the error the
    // issue forbids. Now they must degrade like any other unknown type.
    render(<A2UIRenderer items={[{ type: "toString", props: {} }]} />);
    expect(screen.getByText("Unknown component: toString")).toBeTruthy();
    expect(screen.getByTestId("a2ui-fallback")).toBeTruthy();
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(console.error).toHaveBeenCalledWith(
      '[a2ui] unknown component type "toString" — rendered fallback card',
    );
  });

  it('renders "Invalid component: <type>" once for a known type with invalid props', () => {
    render(<A2UIRenderer items={[{ type: "kpi_row", props: {} }]} />);
    expect(screen.getByText("Invalid component: kpi_row")).toBeTruthy();
    expect(console.error).toHaveBeenCalledTimes(1);
  });

  it("renders one fallback for a garbage item without crashing, keeping siblings", () => {
    render(<A2UIRenderer items={[{ nonsense: true }]} />);
    expect(screen.getAllByTestId("a2ui-fallback")).toHaveLength(1);
    expect(screen.getByText("Invalid component")).toBeTruthy();
    expect(console.error).toHaveBeenCalledTimes(1);
  });
});

describe("A2UIRenderer (layout path)", () => {
  it("renders the PRD §10 layout as one real component per item, silently", () => {
    render(
      <A2UIRenderer
        layout={{
          v: 1,
          layout: "grid",
          items: [
            KPI_ROW_ITEM,
            { type: "insight_text", props: { title: "T", body: "B" } },
          ],
          slotCopy: { "kpis.title": "Q3 revenue took off" },
        }}
      />,
    );
    expect(screen.getByTestId("catalog-kpi_row")).toBeTruthy();
    expect(screen.getByTestId("catalog-insight_text")).toBeTruthy();
    expect(console.error).not.toHaveBeenCalled();
  });

  it('renders a single "Invalid layout" card for a layout failing Zod validation, with no crash', () => {
    render(<A2UIRenderer layout={{ v: 2, layout: "grid", items: [] }} />);
    const fallbacks = screen.getAllByTestId("a2ui-fallback");
    expect(fallbacks).toHaveLength(1);
    expect(fallbacks[0].textContent).toContain("Invalid layout");
  });

  it('renders a single "Invalid layout" card when items is not an array', () => {
    render(<A2UIRenderer layout={{ v: 1, layout: "grid", items: "nope" }} />);
    expect(screen.getAllByTestId("a2ui-fallback")).toHaveLength(1);
    expect(screen.getByText("Invalid layout")).toBeTruthy();
    expect(console.error).toHaveBeenCalledTimes(1);
  });

  it("renders nothing for an empty valid layout and for no props at all", () => {
    const { container: emptyLayout } = render(
      <A2UIRenderer layout={{ v: 1, layout: "grid", items: [] }} />,
    );
    expect(emptyLayout.firstChild).toBeNull();
    const { container: nothing } = render(<A2UIRenderer />);
    expect(nothing.firstChild).toBeNull();
  });
});

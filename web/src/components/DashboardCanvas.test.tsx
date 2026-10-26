// Acceptance 1 (issue #12): the demo canvas mounts with a fixture layout
// containing all six types, and the DOM contains exactly 6 registry-resolved
// components — no fallback cards, no placeholders, no console errors.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardCanvas } from "./DashboardCanvas";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DashboardCanvas (acceptance 1)", () => {
  it("renders exactly 6 registry-resolved catalog components, no fallback cards", () => {
    const { container } = render(<DashboardCanvas />);
    const resolved = container.querySelectorAll('[data-testid^="catalog-"]');
    expect(resolved).toHaveLength(6);
    expect(container.querySelector('[data-testid="a2ui-fallback"]')).toBeNull();
    expect(
      container.querySelector('[data-testid^="catalog-placeholder-"]'),
    ).toBeNull();
    expect(console.error).not.toHaveBeenCalled();
  });

  it("renders all six catalog testids", () => {
    render(<DashboardCanvas />);
    for (const type of [
      "kpi_row",
      "line_chart",
      "variance_callout",
      "data_table",
      "data_quality_card",
      "insight_text",
    ]) {
      expect(screen.getByTestId(`catalog-${type}`)).toBeTruthy();
    }
  });

  it("exposes the acceptance KPI formatted and the 92-point Q3 chart", () => {
    render(<DashboardCanvas />);
    expect(screen.getByText("$1.20M")).toBeTruthy();
    expect(screen.getByText("+18%")).toBeTruthy();
    expect(screen.getByTestId("line-chart").getAttribute("data-points")).toBe("92");
  });

  it("carries the 50-row demo table with a working segment filter", async () => {
    const user = userEvent.setup();
    render(<DashboardCanvas />);
    expect(screen.getAllByTestId("data-table-row")).toHaveLength(50);
    await user.type(screen.getByRole("textbox", { name: "Filter segment" }), "Growth");
    const rows = screen.getAllByTestId("data-table-row");
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row.textContent).toContain("Growth");
    }
  });

  it("renders the degraded canvas for an invalid layout without crashing", () => {
    const { container } = render(<DashboardCanvas layout={{ v: 2 }} />);
    expect(container.querySelector('[data-testid="a2ui-fallback"]')).not.toBeNull();
  });

  it("renders a streamed layout in place of the fixture (issue #17): DQ card only, banner present", () => {
    // What the "Is the data clean?" run streams: a single data_quality_card
    // stamped with the noul data_problem probability and the narrated
    // anomaly detail — App passes it straight through App → DashboardCanvas.
    const streamed = {
      v: 1 as const,
      layout: "grid" as const,
      items: [
        {
          type: "data_quality_card",
          props: {
            checks: [
              { name: "freshness", status: "ok", score: 1 },
              { name: "duplicates", status: "fail", score: 0.87 },
              { name: "nulls", status: "fail", score: 0.95 },
            ],
            data_problem: 0.94,
            data_problem_detail:
              "duplicate and null usage rows cluster in Sep 8-21, 2026 (2026-09-08..2026-09-21), Startup segment",
          },
        },
      ],
      slotCopy: {
        "data_quality_card.summary":
          "Data-quality checks failing: duplicates, nulls.",
      },
    };
    const { container } = render(<DashboardCanvas layout={streamed} />);
    // The streamed layout REPLACES the fixture: only its own item renders.
    const resolved = container.querySelectorAll('[data-testid^="catalog-"]');
    expect(resolved).toHaveLength(1);
    expect(screen.getByTestId("catalog-data_quality_card")).toBeTruthy();
    expect(screen.queryByTestId("catalog-kpi_row")).toBeNull();
    // The banner half of the DOM acceptance: visible warning with the Sept
    // evidence and the Report-an-issue affordance.
    const banner = screen.getByTestId("dq-warning");
    expect(banner.textContent).toContain("Sep 8-21, 2026");
    expect(screen.getByTestId("dq-report").textContent).toContain(
      "Report an issue",
    );
  });
});

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
});

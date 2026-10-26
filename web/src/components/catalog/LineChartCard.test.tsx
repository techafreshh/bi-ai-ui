// LineChartCard behavior tests (issue #12). Pinned by the acceptance
// scenario: arr_daily over 2026-07-01..2026-09-30 produces a Recharts SVG
// with ≥90 data points (the data-points attribute exposes the exact count).

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LineChartCard } from "./LineChartCard";

afterEach(() => {
  cleanup();
});

describe("LineChartCard", () => {
  it("mounts one Recharts SVG surface carrying all 92 Q3 points", () => {
    const { container } = render(
      <LineChartCard series="arr_daily" range="2026-07-01..2026-09-30" />,
    );
    expect(container.querySelector("svg.recharts-surface")).not.toBeNull();
    const chart = screen.getByTestId("line-chart");
    expect(chart.getAttribute("data-points")).toBe("92");
    expect(screen.getByTestId("catalog-line_chart")).toBeTruthy();
  });

  it("renders an in-card empty state for an unknown series, not a fallback", () => {
    render(<LineChartCard series="mystery" range="2026-07-01..2026-09-30" />);
    expect(screen.getByText("No data for this series")).toBeTruthy();
    expect(screen.getByTestId("catalog-line_chart")).toBeTruthy();
    expect(screen.queryByTestId("a2ui-fallback")).toBeNull();
    expect(screen.queryByTestId("line-chart")).toBeNull();
  });

  it("lists annotations as a muted footer", () => {
    render(
      <LineChartCard
        series="arr_daily"
        range="2026-07-01..2026-07-10"
        annotations={["Aug 10: pricing change"]}
      />,
    );
    expect(screen.getByText("Aug 10: pricing change")).toBeTruthy();
  });
});

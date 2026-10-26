// KpiRow behavior tests (issue #12). Pinned by the acceptance scenario:
// a kpi_row with {label: "Net-new ARR", value: 1200000, delta: 0.18} shows
// "$1.20M" and "+18%" — formatted by formatCurrency/formatDelta through the
// beUI AnimatedNumber (the reduced-motion polyfill settles the final frame
// synchronously).

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { KpiRow } from "./KpiRow";

afterEach(() => {
  cleanup();
});

// The PRD §10 KPI, verbatim.
const KPI = { label: "Net-new ARR", value: 1200000, delta: 0.18 };

describe("KpiRow", () => {
  it("renders the acceptance KPI with formatted value and delta", () => {
    render(<KpiRow kpis={[KPI]} />);
    expect(screen.getByText("Net-new ARR")).toBeTruthy();
    expect(screen.getByText("$1.20M")).toBeTruthy();
    expect(screen.getByText("+18%")).toBeTruthy();
    expect(screen.getByTestId("catalog-kpi_row")).toBeTruthy();
  });

  it("renders a loss with the minus form and no plus", () => {
    render(<KpiRow kpis={[{ label: "Churn", value: 42_000, delta: -0.03 }]} />);
    expect(screen.getByText("-3%")).toBeTruthy();
    expect(screen.getByText("$42.0K")).toBeTruthy();
  });

  it("renders one tile per KPI entry", () => {
    render(
      <KpiRow
        kpis={[
          KPI,
          { label: "MRR", value: 98_500, delta: 0.04 },
          { label: "Active accounts", value: 1_240, delta: 0.02 },
          { label: "NRR", value: 1.12, delta: 0.01 },
        ]}
      />,
    );
    expect(screen.getAllByText("vs prior period")).toHaveLength(4);
  });
});

// VarianceCallout behavior tests (issue #12): metric/delta/baseline headline,
// cause body, humanized Investigate CTA that logs the a2ui decision.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VarianceCallout } from "./VarianceCallout";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const CALLOUT = {
  metric: "MRR churn",
  delta: 0.42,
  baseline: "the trailing 90 days",
  cause: "Aug 10 pricing change pushed a Growth-segment cohort to cancel.",
  cta: "churn cohort",
};

describe("VarianceCallout", () => {
  it("renders metric, formatted delta, baseline and cause", () => {
    render(<VarianceCallout {...CALLOUT} />);
    expect(screen.getByTestId("catalog-variance_callout")).toBeTruthy();
    // The headline is built from pieces across spans; assert via their nodes.
    const headline = screen.getByTestId("variance-metric");
    expect(headline.textContent).toContain("MRR churn");
    expect(headline.textContent).toContain("vs the trailing 90 days");
    expect(screen.getByTestId("variance-delta").textContent).toBe("+42%");
    expect(screen.getByText(CALLOUT.cause)).toBeTruthy();
  });

  it("renders a warning-level alert (role=alert)", () => {
    render(<VarianceCallout {...CALLOUT} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("humanizes the CTA as 'Investigate <cta>' and logs the investigation", async () => {
    const user = userEvent.setup();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const onAction = vi.fn();
    render(<VarianceCallout {...CALLOUT} onAction={onAction} />);
    const cta = screen.getByRole("button", { name: "Investigate churn cohort" });
    expect(cta).toBeTruthy();
    await user.click(cta);
    expect(info).toHaveBeenCalledWith(
      "[a2ui] investigate: MRR churn — churn cohort",
    );
    expect(onAction).toHaveBeenCalledWith("churn cohort");
  });

  it("falls back to the default baseline phrasing when absent, and drops the CTA", () => {
    render(<VarianceCallout metric="MRR" delta={-0.12} />);
    expect(screen.getByTestId("variance-metric").textContent).toContain("vs the prior period");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

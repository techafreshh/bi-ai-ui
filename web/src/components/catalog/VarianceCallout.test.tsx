// VarianceCallout behavior tests (issue #12/#16): metric/delta/baseline
// headline, cause body, humanized Investigate CTA that logs the a2ui
// decision, and — below an InvestigateProvider — pressing the CTA routes the
// raw machine cta id into the context handler (the framed follow-up send)
// while onAction still fires. Without a provider the handler is null and the
// press stays log-only.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvestigateProvider } from "../../hooks/useInvestigate";
import { VarianceCallout } from "./VarianceCallout";

// Hoisted before the component imports: every consumer of the CopilotKit v2
// module in this file (InvestigateProvider's useAgent included) resolves the
// stub — one module instance, one shared React context. Swapped per-test.
let agent: unknown;
vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  agent = null;
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

  it("humanizes the agent's investigate_churn_cohort machine cta id", () => {
    render(<VarianceCallout {...CALLOUT} cta="investigate_churn_cohort" />);
    expect(screen.getByRole("button", { name: "Investigate churn cohort" })).toBeTruthy();
  });

  it("presses the investigate handler (provider-bound) and still fires onAction with the raw cta", async () => {
    const addMessage = vi.fn();
    const runAgent = vi.fn(() => Promise.resolve({}));
    agent = {
      messages: [
        { id: "m1", role: "user", content: "Why did churn spike in August?" },
      ],
      isRunning: false,
      addMessage,
      runAgent,
    };
    const onAction = vi.fn();
    render(
      <InvestigateProvider agentId="bi_agent">
        <VarianceCallout {...CALLOUT} cta="investigate_churn_cohort" onAction={onAction} />
      </InvestigateProvider>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Investigate churn cohort" }));
    // The framed follow-up went to the shared agent thread...
    expect(addMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        role: "user",
        content: expect.stringContaining("[investigate] churn cohort — Why did churn spike"),
      }),
    );
    expect(runAgent).toHaveBeenCalledTimes(1);
    // ...and the demo-style onAction hook still saw the raw machine id.
    expect(onAction).toHaveBeenCalledWith("investigate_churn_cohort");
  });
});

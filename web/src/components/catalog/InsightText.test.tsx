// InsightText behavior tests (issue #12): title and body render on the muted
// beUI Card; registry testid present (the type-exactness contract lives in
// registry.test.ts and DashboardCanvas.test.tsx).

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { InsightText } from "./InsightText";

afterEach(() => {
  cleanup();
});

describe("InsightText", () => {
  it("renders the narrative title and body", () => {
    render(
      <InsightText
        title="Q3 summary"
        body="Net-new ARR accelerated through Q3, with the Aug 10 pricing change leaving a visible churn dent."
      />,
    );
    expect(screen.getByTestId("catalog-insight_text")).toBeTruthy();
    expect(screen.getByText("Q3 summary")).toBeTruthy();
    expect(
      screen.getByText(/Net-new ARR accelerated through Q3/),
    ).toBeTruthy();
  });
});

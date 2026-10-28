// Acceptance 1 (issue #12): the demo canvas mounts with a fixture layout
// containing all six types, and the DOM contains exactly 6 registry-resolved
// components — no fallback cards, no placeholders, no console errors.

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardCanvas } from "./DashboardCanvas";
import { DEMO_LAYOUT } from "../lib/demoLayout";
import {
  FakeAgent,
  getFakeAgent,
  setFakeAgent,
} from "../test/fakeAgent";

// Whole-module replace: the canvas only consumes useAgent (via useA2UI),
// so the stub keeps the CopilotKit runtime out of the suite.
vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent: getFakeAgent(), isReady: true }),
}));

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  setFakeAgent(new FakeAgent());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DashboardCanvas (acceptance 1)", () => {
  it("renders the empty state by default with no dataset", () => {
    const { container } = render(<DashboardCanvas />);
    expect(screen.getByTestId("empty-state")).toBeTruthy();
    expect(
      screen.getByText("No dataset loaded — upload CSV or load sample"),
    ).toBeTruthy();
    expect(container.querySelector('[data-testid="canvas-skeleton"]')).toBeNull();
    expect(
      container.querySelectorAll('[data-testid^="catalog-"]'),
    ).toHaveLength(0);
  });

  it("renders exactly 6 registry-resolved catalog components, no fallback cards", () => {
    const { container } = render(<DashboardCanvas layout={DEMO_LAYOUT} />);
    const resolved = container.querySelectorAll('[data-testid^="catalog-"]');
    expect(resolved).toHaveLength(6);
    expect(container.querySelector('[data-testid="a2ui-fallback"]')).toBeNull();
    expect(
      container.querySelector('[data-testid^="catalog-placeholder-"]'),
    ).toBeNull();
    expect(console.error).not.toHaveBeenCalled();
  });

  it("renders all six catalog testids", () => {
    render(<DashboardCanvas layout={DEMO_LAYOUT} />);
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
    render(<DashboardCanvas layout={DEMO_LAYOUT} />);
    expect(screen.getByText("$1.20M")).toBeTruthy();
    expect(screen.getByText("+18%")).toBeTruthy();
    expect(screen.getByTestId("line-chart").getAttribute("data-points")).toBe("92");
  });

  it("carries the 50-row demo table with a working segment filter", async () => {
    const user = userEvent.setup();
    render(<DashboardCanvas layout={DEMO_LAYOUT} />);
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

describe("DashboardCanvas stream phases (acceptance 13)", () => {
  const STREAMED = {
    v: 1 as const,
    layout: "grid" as const,
    items: [
      {
        type: "kpi_row",
        props: {
          kpis: [
            { label: "Net-new ARR (2026-07-01..2026-09-30)", value: 1200000, delta: 0.18 },
          ],
        },
      },
      {
        type: "insight_text",
        props: { title: "Q3 summary", body: "Draft body." },
      },
    ],
  };

  it("shows the skeleton grid within the same tick as the run starts", () => {
    const { container } = render(<DashboardCanvas layout={DEMO_LAYOUT} />);
    expect(container.querySelector('[data-testid="canvas-skeleton"]')).toBeNull();
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    const skeleton = container.querySelector('[data-testid="canvas-skeleton"]');
    expect(skeleton).not.toBeNull();
    expect(screen.getByTestId("dashboard-canvas")).toBeTruthy();
  });

  it("renders live items with slot skeletons, then copy replaces them", () => {
    const { container } = render(<DashboardCanvas layout={DEMO_LAYOUT} />);
    act(() => {
      getFakeAgent().emitRunStarted();
      getFakeAgent().emitState({ a2ui: STREAMED });
    });
    // Layout before the text model: live tiles plus pending slots.
    expect(screen.getByTestId("catalog-kpi_row")).toBeTruthy();
    expect(screen.getByText(/Net-new ARR/)).toBeTruthy();
    expect(
      container.querySelectorAll('[data-testid="slot-skeleton"]').length,
    ).toBeGreaterThan(0);
    // Fill: the same snapshot with slotCopy swaps skeletons for copy.
    act(() => {
      getFakeAgent().emitState({
        a2ui: {
          ...STREAMED,
          slotCopy: {
            "line_chart.title": "Daily ARR through Q3",
            "insight_text.body": "Net-new ARR compounded through Q3.",
          },
        },
      });
    });
    expect(container.querySelector('[data-testid="slot-skeleton"]')).toBeNull();
    expect(screen.getByText("Net-new ARR compounded through Q3.")).toBeTruthy();
  });

  it("degrades an invalid streamed envelope to the fallback card, not a stuck skeleton", () => {
    const { container } = render(<DashboardCanvas layout={DEMO_LAYOUT} />);
    act(() => {
      getFakeAgent().emitRunStarted();
      getFakeAgent().emitState({ a2ui: { v: 2 } });
    });
    expect(container.querySelector('[data-testid="canvas-skeleton"]')).toBeNull();
    expect(container.querySelector('[data-testid="a2ui-fallback"]')).not.toBeNull();
  });

  it("a run ending with no layout returns to the empty canvas", () => {
    const { container } = render(<DashboardCanvas />);
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    expect(container.querySelector('[data-testid="canvas-skeleton"]')).not.toBeNull();
    act(() => {
      getFakeAgent().emitRunFinalized();
    });
    expect(container.querySelector('[data-testid="canvas-skeleton"]')).toBeNull();
    expect(screen.getByTestId("empty-state")).toBeTruthy();
    expect(
      container.querySelectorAll('[data-testid^="catalog-"]'),
    ).toHaveLength(0);
  });

  it("a run ending with no layout returns to the passed fixture canvas", () => {
    const { container } = render(<DashboardCanvas layout={DEMO_LAYOUT} />);
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    expect(container.querySelector('[data-testid="canvas-skeleton"]')).not.toBeNull();
    act(() => {
      getFakeAgent().emitRunFinalized();
    });
    expect(container.querySelector('[data-testid="canvas-skeleton"]')).toBeNull();
    expect(container.querySelectorAll('[data-testid^="catalog-"]')).toHaveLength(6);
  });
});

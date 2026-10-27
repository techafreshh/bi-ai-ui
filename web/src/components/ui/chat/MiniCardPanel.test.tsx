// Issue #13: MiniCardPanel behavior — the last-bubble mini-card renders the
// streamed kpi_row compactly, the 200px budget is bounded, unknown items
// degrade in-panel, and the LatestMiniCard gate only opens for the latest
// message with a filled layout.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LatestMiniCard, MiniCardPanel } from "./MiniCardPanel";

// MiniCardPanel imports slotCopyOf from hooks/useA2UI, whose module chain
// pulls the CopilotKit runtime (and its .css, which vitest cannot load) —
// the same whole-module stub the catalog suites use. The panel itself never
// calls the hook, so the stub body never runs.
vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent: null, isReady: false }),
}));

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const GRAPH_LAYOUT = {
  v: 1 as const,
  layout: "grid" as const,
  items: [
    {
      type: "kpi_row",
      props: {
        kpis: [
          { label: "Net-new ARR (2026-07-01..2026-09-30)", value: 1200000, delta: 0.18 },
          { label: "MRR (2026-07-01..2026-09-30)", value: 98500, delta: 0.04 },
        ],
      },
    },
    {
      type: "line_chart",
      props: { series: "arr_daily", range: "2026-07-01..2026-09-30" },
    },
  ],
  slotCopy: { "line_chart.title": "Daily ARR through Q3" },
};

describe("MiniCardPanel", () => {
  it("renders the streamed kpi_row with the Net-new ARR label", () => {
    render(<MiniCardPanel layout={GRAPH_LAYOUT} />);
    const panel = screen.getByTestId("chat-minicard");
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain("Net-new ARR");
    expect(screen.getByTestId("catalog-kpi_row")).toBeTruthy();
  });

  it("renders the envelope's filled copy in the mini panel, not the fallback", () => {
    render(<MiniCardPanel layout={GRAPH_LAYOUT} />);
    const panel = screen.getByTestId("chat-minicard");
    expect(panel.textContent).toContain("Daily ARR through Q3");
    expect(panel.textContent).not.toContain("ARR, daily");
  });

  it("stays within the 200px budget with internal scroll", () => {
    render(<MiniCardPanel layout={GRAPH_LAYOUT} />);
    const panel = screen.getByTestId("chat-minicard");
    expect(panel.className).toContain("max-h-[200px]");
    expect(panel.className).toContain("overflow-y-auto");
  });

  it("degrades an unknown item to an in-panel fallback", () => {
    render(
      <MiniCardPanel
        layout={{ v: 1, layout: "grid", items: [{ type: "nope", props: {} }] }}
      />,
    );
    const panel = screen.getByTestId("chat-minicard");
    expect(panel.querySelector('[data-testid="a2ui-fallback"]')).not.toBeNull();
  });
});

describe("LatestMiniCard gate", () => {
  it("shows the mini-card for the latest message with a filled layout", () => {
    render(<LatestMiniCard isLatest a2ui={GRAPH_LAYOUT} phase="filled" />);
    expect(screen.getByTestId("chat-minicard")).toBeTruthy();
  });

  it("shows nothing during the layout phase (copy still pending)", () => {
    const { container } = render(
      <LatestMiniCard isLatest a2ui={GRAPH_LAYOUT} phase="layout" />,
    );
    expect(container.querySelector('[data-testid="chat-minicard"]')).toBeNull();
  });

  it("shows nothing for non-latest messages", () => {
    const { container } = render(
      <LatestMiniCard isLatest={false} a2ui={GRAPH_LAYOUT} phase="filled" />,
    );
    expect(container.querySelector('[data-testid="chat-minicard"]')).toBeNull();
  });

  it("shows nothing when no layout has streamed yet", () => {
    const { container } = render(
      <LatestMiniCard isLatest a2ui={null} phase="pending" />,
    );
    expect(container.querySelector('[data-testid="chat-minicard"]')).toBeNull();
  });
});

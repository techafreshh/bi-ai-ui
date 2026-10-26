// DataQualityCard behavior tests (issue #12): the variant ladder drives the
// beUI Alert's ARIA role — any fail -> destructive (role=alert), all-ok ->
// success (role=status) — and each check renders its name and score.
// Issue #17: the data_problem gate drives the dq-warning banner (p > 0.6,
// narrated evidence with "Sep", Report-an-issue affordance that console-
// logs and toasts, auto-dismissing after 4s).

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataQualityCard, type QualityCheck } from "./DataQualityCard";

afterEach(() => {
  cleanup();
});

const CHECKS: QualityCheck[] = [
  { name: "freshness", status: "ok", score: 0.97 },
  { name: "duplicates", status: "fail", score: 0.81 },
  { name: "nulls", status: "warn", score: 0.93 },
];

const ALL_OK: QualityCheck[] = [
  { name: "freshness", status: "ok", score: 1 },
  { name: "duplicates", status: "ok", score: 1 },
  { name: "nulls", status: "ok", score: 0.99 },
];

// The agent-narrated evidence line (graph.py's stamp): Sept window + segment.
const DETAIL =
  "duplicate and null usage rows cluster in Sep 8-21, 2026 (2026-09-08..2026-09-21), Startup segment";

describe("DataQualityCard", () => {
  it("renders the fixture check rows with names and scores", () => {
    render(<DataQualityCard checks={CHECKS} />);
    expect(screen.getByTestId("catalog-data_quality_card")).toBeTruthy();
    expect(screen.getByText("Freshness")).toBeTruthy();
    expect(screen.getByText("Duplicates")).toBeTruthy();
    expect(screen.getByText("Nulls")).toBeTruthy();
    expect(screen.getByText("97% confidence")).toBeTruthy();
    expect(screen.getByText("81% confidence")).toBeTruthy();
  });

  it("escalates to destructive (role=alert) when any check fails", () => {
    render(<DataQualityCard checks={CHECKS} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Data quality issues detected")).toBeTruthy();
  });

  it("marks warn-only fixtures as warning (role=alert)", () => {
    render(
      <DataQualityCard
        checks={[{ name: "freshness", status: "warn", score: 0.9 }]}
      />,
    );
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Data quality needs attention")).toBeTruthy();
  });

  it("treats all-ok fixtures as success (role=status)", () => {
    render(<DataQualityCard checks={ALL_OK} />);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Data is clean")).toBeTruthy();
  });

  it("shows the dq-warning banner when data_problem > 0.6, with the evidence and CTA", () => {
    render(
      <DataQualityCard
        checks={CHECKS}
        data_problem={0.94}
        data_problem_detail={DETAIL}
      />,
    );
    const banner = screen.getByTestId("dq-warning");
    expect(banner.textContent).toContain("Data problem suspected");
    // The banner carries the narrated window (the acceptance's "Sep") and
    // segment evidence verbatim.
    expect(banner.textContent).toContain("Sep 8-21, 2026");
    expect(banner.textContent).toContain("Startup segment");
    const button = screen.getByTestId("dq-report");
    expect(button.textContent).toContain("Report an issue");
  });

  it.each([
    ["exactly at the 0.6 gate", 0.6],
    ["just below the gate", 0.59],
    ["absent", undefined],
    ["null", null],
    ["a non-finite transport leak", Number.NaN],
  ])("hides the banner when data_problem is %s", (_label, problem) => {
    render(
      <DataQualityCard
        checks={CHECKS}
        data_problem={problem}
        data_problem_detail={DETAIL}
      />,
    );
    expect(screen.queryByTestId("dq-warning")).toBeNull();
    expect(screen.queryByTestId("dq-report")).toBeNull();
  });

  it("falls back to generic banner copy without a narrated detail", () => {
    render(<DataQualityCard checks={CHECKS} data_problem={0.94} />);
    const banner = screen.getByTestId("dq-warning");
    expect(banner.textContent).toContain("Data problem suspected");
    expect(banner.textContent).toContain("usage rows in the recent window");
  });

  it("Report-an-issue console-logs and toasts, auto-dismissing after 4s", () => {
    // fireEvent (not userEvent — its input pipeline stalls under frozen
    // timers): the click is synchronous, the 4s toast timer is the only
    // clock the test needs to move.
    vi.useFakeTimers();
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    try {
      render(
        <DataQualityCard
          checks={CHECKS}
          data_problem={0.94}
          data_problem_detail={DETAIL}
        />,
      );
      expect(screen.queryByTestId("dq-report-toast")).toBeNull();
      fireEvent.click(screen.getByTestId("dq-report"));
      // The affordance is log + toast only — no backend endpoint.
      expect(log).toHaveBeenCalledWith(
        expect.stringContaining("[a2ui] report-an-issue"),
      );
      expect(log).toHaveBeenCalledWith(expect.stringContaining("Sep 8-21, 2026"));
      const toast = screen.getByTestId("dq-report-toast");
      expect(toast.getAttribute("role")).toBe("status");
      // The toast survives just under its 4s lifetime...
      act(() => {
        vi.advanceTimersByTime(3999);
      });
      expect(screen.getByTestId("dq-report-toast")).toBeTruthy();
      // ...and self-dismisses exactly at 4s.
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(screen.queryByTestId("dq-report-toast")).toBeNull();
    } finally {
      vi.useRealTimers();
      log.mockRestore();
    }
  });
});

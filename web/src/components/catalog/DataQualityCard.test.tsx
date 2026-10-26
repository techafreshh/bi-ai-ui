// DataQualityCard behavior tests (issue #12): the variant ladder drives the
// beUI Alert's ARIA role — any fail -> destructive (role=alert), all-ok ->
// success (role=status) — and each check renders its name and score.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
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
});

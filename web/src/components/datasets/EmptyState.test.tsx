// Issue #70: the empty state renders the exact contract text with no
// dataset — the same string the acceptance probe greps for on `/`.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EmptyState } from "./EmptyState";

afterEach(() => {
  cleanup();
});

describe("EmptyState (issue #70)", () => {
  it("renders the exact contract text", () => {
    render(<EmptyState />);
    expect(
      screen.getByText("No dataset loaded — upload CSV or load sample"),
    ).toBeTruthy();
  });

  it("exposes the empty-state testid and no catalog components", () => {
    const { container } = render(<EmptyState />);
    expect(screen.getByTestId("empty-state")).toBeTruthy();
    expect(
      container.querySelectorAll('[data-testid^="catalog-"]'),
    ).toHaveLength(0);
  });
});

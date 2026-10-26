// DataTableCard behavior tests (issue #12). Pinned by the acceptance
// scenario: a 50-row table renders 50 row elements and the `segment` column
// carries a filter input that, on typing "Growth", leaves only Growth rows.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { DataTableCard } from "./DataTableCard";

afterEach(() => {
  cleanup();
});

// 50 rows across three segments (seeded, no RNG).
const ROWS = Array.from({ length: 50 }, (_, index) => ({
  account: `Account ${String(index + 1).padStart(2, "0")}`,
  segment: index % 3 === 0 ? "Growth" : index % 3 === 1 ? "Enterprise" : "Startup",
  mrr: 900 + index * 37,
}));

describe("DataTableCard", () => {
  it("renders all 50 rows and a filter input for the segment column", () => {
    render(<DataTableCard columns={["account", "segment", "mrr"]} rows={ROWS} filters={["segment"]} />);
    expect(screen.getAllByTestId("data-table-row")).toHaveLength(50);
    expect(screen.getByRole("textbox", { name: "Filter segment" })).toBeTruthy();
    expect(screen.getByTestId("catalog-data_table")).toBeTruthy();
  });

  it("typing Growth into the segment filter leaves only Growth rows", async () => {
    const user = userEvent.setup();
    render(<DataTableCard columns={["account", "segment", "mrr"]} rows={ROWS} filters={["segment"]} />);
    await user.type(screen.getByRole("textbox", { name: "Filter segment" }), "Growth");
    const rows = screen.getAllByTestId("data-table-row");
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThan(50);
    for (const row of rows) {
      expect(row.textContent).toContain("Growth");
    }
  });

  it("keeps the full row set when the filter is cleared", async () => {
    const user = userEvent.setup();
    render(<DataTableCard columns={["account", "segment", "mrr"]} rows={ROWS} filters={["segment"]} />);
    const input = screen.getByRole("textbox", { name: "Filter segment" });
    await user.type(input, "Growth");
    await user.type(input, "{backspace}".repeat("Growth".length));
    expect(screen.getAllByTestId("data-table-row")).toHaveLength(50);
  });

  it("shows the in-card empty state when no row matches", async () => {
    const user = userEvent.setup();
    render(<DataTableCard columns={["account", "segment"]} rows={ROWS.slice(0, 3)} filters={["account"]} />);
    await user.type(screen.getByRole("textbox", { name: "Filter account" }), "no-such-row");
    expect(screen.getByText("No data")).toBeTruthy();
    expect(screen.queryByTestId("data-table-row")).toBeNull();
  });
});

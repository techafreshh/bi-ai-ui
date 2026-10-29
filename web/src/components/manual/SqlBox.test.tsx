// Issue #84: SqlBox behavior tests — table render on 200, blocked-as-
// insight_text on 400, and the exact POST /data/query contract. fetch is
// stubbed per test in the DatasetPicker/MetricPicker suite style.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SqlBox } from "./SqlBox";

function stubFetch(impl: (url: string, init?: RequestInit) => unknown) {
  const stub = vi.fn(
    async (url: unknown, init?: unknown) =>
      impl(url as string, init as RequestInit),
  );
  vi.stubGlobal("fetch", stub);
  return stub;
}

function okJson(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload };
}

function errJson(status: number, payload: unknown) {
  return { ok: false, status, json: async () => payload };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("SqlBox (issue #84)", () => {
  it("entering the group-by SELECT and Run renders data-table with the segment header", async () => {
    stubFetch(() =>
      okJson({
        columns: ["segment", "COUNT(*)"],
        rows: [
          ["Enterprise", 12],
          ["Growth", 34],
        ],
      }),
    );
    const user = userEvent.setup();
    render(<SqlBox />);
    const input = screen.getByTestId("sql-input");
    await user.clear(input);
    await user.type(
      input,
      "SELECT segment, COUNT(*) FROM accounts GROUP BY segment",
    );
    await user.click(screen.getByTestId("sql-run"));
    const table = await screen.findByTestId("data-table");
    expect(table.textContent?.toLowerCase()).toContain("segment");
    expect(screen.getByTestId("sql-result")).toBeTruthy();
    expect(screen.getByTestId("manual-badge").textContent).toBe("manual");
    expect(screen.queryByTestId("a2ui-fallback")).toBeNull();
  });

  it("dict rows render verbatim through the existing data_table", async () => {
    stubFetch(() =>
      okJson({
        columns: ["segment"],
        rows: [{ segment: "Growth" }, { segment: "Startup" }],
      }),
    );
    const user = userEvent.setup();
    render(<SqlBox />);
    await user.click(screen.getByTestId("sql-run"));
    const table = await screen.findByTestId("data-table");
    expect(table.textContent).toContain("Growth");
    expect(screen.queryByTestId("a2ui-fallback")).toBeNull();
  });

  it("sends the exact POST /data/query contract as JSON", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    stubFetch((url, init) => {
      calls.push({ url, init });
      return okJson({ columns: ["segment"], rows: [{ segment: "x" }] });
    });
    const user = userEvent.setup();
    render(<SqlBox />);
    const input = screen.getByTestId("sql-input");
    await user.clear(input);
    await user.type(input, "SELECT segment FROM accounts");
    await user.click(screen.getByTestId("sql-run"));
    await screen.findByTestId("sql-result");
    expect(calls).toHaveLength(1);
    expect(calls[0].url.endsWith("/data/query")).toBe(true);
    expect(calls[0].init?.method).toBe("POST");
    const contentType =
      (calls[0].init?.headers as Record<string, string> | undefined)?.[
        "content-type"
      ] ??
      (calls[0].init?.headers instanceof Headers
        ? (calls[0].init?.headers as Headers).get("content-type")
        : null);
    expect(contentType).toContain("application/json");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      sql: "SELECT segment FROM accounts",
    });
  });

  it("a 400 blocked response renders insight-text with the message, not a crash", async () => {
    stubFetch(() => errJson(400, { error: "blocked: only leading SELECT statements are allowed" }));
    const user = userEvent.setup();
    render(<SqlBox />);
    const input = screen.getByTestId("sql-input");
    await user.clear(input);
    await user.type(input, "DROP TABLE accounts");
    await user.click(screen.getByTestId("sql-run"));
    const insight = await screen.findByTestId("insight-text");
    expect(insight.textContent).toContain("blocked");
    expect(screen.getByTestId("catalog-insight_text").textContent).toContain(
      "blocked",
    );
    expect(screen.queryByTestId("manual-error")).toBeNull();
    expect(screen.queryByTestId("a2ui-fallback")).toBeNull();
    expect(screen.queryByTestId("data-table")).toBeNull();
  });
});

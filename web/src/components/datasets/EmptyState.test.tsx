// Issue #70: the empty state renders the exact contract text with no
// dataset — the same string the acceptance probe greps for on `/`.
//
// Issue #76: the empty canvas owns the load loop — dropzone, file input and
// the one-click `Load sample` path. `globalThis.fetch` is stubbed per test;
// the sample click must fire exactly one POST /data/sample.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmptyState } from "./EmptyState";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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

describe("EmptyState load loop (issue #76)", () => {
  it("exposes the dropzone, file input and Load sample button", () => {
    render(<EmptyState />);
    expect(screen.getByTestId("upload-dropzone")).toBeTruthy();
    const input = screen.getByTestId("upload-input") as HTMLInputElement;
    expect(input.getAttribute("type")).toBe("file");
    expect(input.getAttribute("accept")).toContain(".csv");
    expect(screen.getByTestId("load-sample")).toHaveProperty(
      "textContent",
      "Load sample",
    );
  });

  it("clicking Load sample fires exactly one POST /data/sample", async () => {
    const stub = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        active_id: "sample-1",
        tables: ["accounts", "revenue_events", "subscription_events", "usage_events"],
        rows: 700,
        data_end: "2026-09-30",
      }),
    }));
    vi.stubGlobal("fetch", stub);
    const user = userEvent.setup();
    const onLoaded = vi.fn();
    render(<EmptyState onLoaded={onLoaded} />);
    await user.click(screen.getByTestId("load-sample"));
    await waitFor(() => expect(stub).toHaveBeenCalledTimes(1));
    const [url, init] = stub.mock.calls[0] as unknown as [
      string,
      RequestInit | undefined,
    ];
    expect(url).toContain("/data/sample");
    expect(init?.method).toBe("POST");
    await waitFor(() => expect(onLoaded).toHaveBeenCalledTimes(1));
  });

  it("rejects a filename outside the table allowlist without fetching", async () => {
    const stub = vi.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({}),
    }));
    vi.stubGlobal("fetch", stub);
    const user = userEvent.setup();
    render(<EmptyState />);
    const input = screen.getByTestId("upload-input") as HTMLInputElement;
    const file = new File(["id,name\n1,x\n"], "ledger.csv", { type: "text/csv" });
    await user.upload(input, file);
    expect(await screen.findByTestId("upload-error")).toBeTruthy();
    expect(stub).not.toHaveBeenCalled();
  });
});

// Issue #76: header picker tests — options render, the profile line carries
// `rows` plus a YYYY-MM-DD date, and switching fires the activation POST.
// `globalThis.fetch` is stubbed per test (same seam the EmptyState suite
// uses); the REST base derives from lib/agent.ts, never from the stub.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DatasetPicker } from "./DatasetPicker";

const LIST_BODY = {
  active_id: "sample-1",
  datasets: [
    {
      id: "sample-1",
      source: "sample",
      tables: ["accounts", "revenue_events", "subscription_events", "usage_events"],
      rows: 700,
      data_end: "2026-09-30",
    },
    {
      id: "upload-2",
      source: "upload",
      tables: ["accounts", "revenue_events", "subscription_events", "usage_events"],
      rows: 30,
      data_end: "2026-01-20",
    },
  ],
};

function stubFetch(impl: (url: string, init?: RequestInit) => unknown) {
  const stub = vi.fn(async (url: unknown, init?: unknown) => impl(url as string, init as RequestInit));
  vi.stubGlobal("fetch", stub);
  return stub;
}

function okJson(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("DatasetPicker (issue #76)", () => {
  it("renders one option per dataset plus the empty No-dataset option", async () => {
    stubFetch(() => okJson(LIST_BODY));
    render(<DatasetPicker />);
    const select = await screen.findByTestId("dataset-select");
    expect(select).toBeTruthy();
    expect(screen.getByRole("option", { name: "No dataset" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "sample-1" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "upload-2" })).toBeTruthy();
    expect((select as HTMLSelectElement).value).toBe("sample-1");
  });

  it("profile line contains rows and a YYYY-MM-DD date", async () => {
    stubFetch(() => okJson(LIST_BODY));
    render(<DatasetPicker />);
    const profile = await screen.findByTestId("dataset-profile");
    expect(profile.textContent).toContain("rows");
    expect(profile.textContent).toMatch(/\d{4}-\d{2}-\d{2}/);
    expect(profile.textContent).toContain("2026-09-30");
  });

  it("switching the select fires the activation POST then refetches", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.endsWith("/data/active")) {
        return okJson({ active_id: "upload-2" });
      }
      return okJson({ ...LIST_BODY, active_id: "upload-2" });
    });
    const user = userEvent.setup();
    render(<DatasetPicker />);
    const select = await screen.findByTestId("dataset-select");
    await user.selectOptions(select, "upload-2");
    await waitFor(() => {
      const posts = calls.filter((call) => call.url.endsWith("/data/active"));
      expect(posts).toHaveLength(1);
    });
    const post = calls.find((call) => call.url.endsWith("/data/active"));
    expect(post?.init?.method).toBe("POST");
    expect(post?.init?.body).toContain("upload-2");
    await waitFor(() => {
      expect(screen.getByTestId("dataset-profile").textContent).toContain(
        "2026-01-20",
      );
    });
  });

  it("renders the empty No-dataset option selected when nothing is active", async () => {
    stubFetch(() => okJson({ active_id: null, datasets: [] }));
    render(<DatasetPicker />);
    const select = (await screen.findByTestId("dataset-select")) as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(screen.queryByTestId("dataset-profile")).toBeNull();
  });
});

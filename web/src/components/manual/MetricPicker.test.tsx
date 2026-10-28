// Issue #82: MetricPicker behavior tests — badge + kpi tile on 200,
// insight_text carrying `unknown metric` on 400, the exact /data/run query
// shape, and quarter buttons driving the ISO inputs. `globalThis.fetch` is
// stubbed per test in the DatasetPicker suite style.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MetricPicker } from "./MetricPicker";

const KPI_LAYOUT = {
  v: 1,
  layout: "grid",
  items: [
    {
      type: "kpi_row",
      props: { kpis: [{ label: "NRR", value: 1.12, delta: 0.03 }] },
    },
  ],
  slotCopy: {},
};

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

describe("MetricPicker (issue #82)", () => {
  it("Run renders the manual badge plus a kpi_row tile for nrr/2026-Q3/Growth", async () => {
    stubFetch(() => okJson(KPI_LAYOUT));
    const user = userEvent.setup();
    render(<MetricPicker />);
    await user.selectOptions(screen.getByTestId("manual-metric"), "nrr");
    await user.click(screen.getByTestId("manual-quarter-q3"));
    await user.selectOptions(screen.getByTestId("manual-segment"), "Growth");
    await user.click(screen.getByTestId("manual-run"));
    const badge = await screen.findByTestId("manual-badge");
    expect(badge.textContent).toBe("manual");
    expect(screen.getByTestId("manual-result")).toBeTruthy();
    expect(screen.getByTestId("catalog-kpi_row")).toBeTruthy();
    expect(screen.getByText("NRR")).toBeTruthy();
  });

  it("sends the exact /data/run query contract", async () => {
    const calls: string[] = [];
    stubFetch((url) => {
      calls.push(url);
      return okJson(KPI_LAYOUT);
    });
    const user = userEvent.setup();
    render(<MetricPicker />);
    await user.click(screen.getByTestId("manual-quarter-q3"));
    await user.click(screen.getByTestId("manual-run"));
    await screen.findByTestId("manual-badge");
    expect(calls).toHaveLength(1);
    expect(calls[0].endsWith("/data/run?metric=nrr&start=2026-07-01&end=2026-09-30&segment=Growth")).toBe(
      true,
    );
  });

  it("a 400 unknown-metric response renders insight_text with the message, not a crash", async () => {
    stubFetch(() =>
      errJson(400, {
        error:
          "unknown metric 'bogus'; must be one of ['active_accounts', 'nrr']",
      }),
    );
    const user = userEvent.setup();
    render(<MetricPicker />);
    await user.click(screen.getByTestId("manual-run"));
    const badge = await screen.findByTestId("manual-badge");
    expect(badge.textContent).toBe("manual");
    const card = await screen.findByTestId("catalog-insight_text");
    expect(card.textContent).toContain("unknown metric");
    expect(screen.queryByTestId("manual-error")).toBeNull();
    expect(screen.queryByTestId("a2ui-fallback")).toBeNull();
  });

  it("quarter buttons drive the ISO inputs (Q3 → 2026-07-01/2026-09-30)", async () => {
    stubFetch(() => okJson(KPI_LAYOUT));
    const user = userEvent.setup();
    render(<MetricPicker />);
    await user.click(screen.getByTestId("manual-quarter-q1"));
    expect(
      (screen.getByTestId("manual-start") as HTMLInputElement).value,
    ).toBe("2026-01-01");
    expect((screen.getByTestId("manual-end") as HTMLInputElement).value).toBe(
      "2026-03-31",
    );
    await user.click(screen.getByTestId("manual-quarter-q3"));
    expect(
      (screen.getByTestId("manual-start") as HTMLInputElement).value,
    ).toBe("2026-07-01");
    expect((screen.getByTestId("manual-end") as HTMLInputElement).value).toBe(
      "2026-09-30",
    );
  });
});

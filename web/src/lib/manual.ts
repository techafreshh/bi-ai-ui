// Issue #82: hand-driven manual-run client. Metric names are the hardcoded
// file-order list from agent/semantics/metrics.yaml (no /data/metrics
// endpoint exists). The REST base reuses the lib/datasets.ts idiom of
// deriving from lib/agent.ts AGENT_URL by stripping `/copilotkit`; server
// 400 bodies are `{error: string}` and surface as error-as-string.

import { apiBase } from "./datasets";

// The eight metric names in agent/semantics/metrics.yaml file order.
export const METRIC_NAMES = [
  "net_new_arr",
  "mrr",
  "nrr",
  "churn_rate",
  "active_accounts",
  "data_quality",
  "gross_churned_arr",
  "active_accounts_eom",
] as const;

export type MetricName = (typeof METRIC_NAMES)[number];

export const SEGMENTS = ["All", "Startup", "Growth", "Enterprise"] as const;

export type Segment = (typeof SEGMENTS)[number];

// (start, end) ISO dates of quarter `quarter` (1-4) of `year` — the same
// arithmetic as agent/questions.py `_quarter_bounds`.
export function quarterBounds(year: number, quarter: number): [string, string] {
  const firstMonth = (quarter - 1) * 3 + 1;
  const lastMonth = firstMonth + 2;
  const pad = (n: number): string => String(n).padStart(2, "0");
  const lastDay = new Date(year, lastMonth, 0).getDate();
  return [
    `${year}-${pad(firstMonth)}-01`,
    `${year}-${pad(lastMonth)}-${pad(lastDay)}`,
  ];
}

export interface RunMetricArgs {
  metric: string;
  start: string;
  end: string;
  segment: string;
}

export type RunMetricResult =
  | { ok: true; data: unknown }
  // `status` is the HTTP status for server rejections and absent for
  // transport failures, so the picker can route 400s to an insight_text
  // card (verbatim server message) and transport errors to an inline note.
  | { ok: false; error: string; status?: number };

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (
      payload !== null &&
      typeof payload === "object" &&
      "error" in payload &&
      typeof (payload as { error: unknown }).error === "string"
    ) {
      return (payload as { error: string }).error;
    }
  } catch {
    // Non-JSON error bodies fall through to the status-text fallback.
  }
  return `${fallback} (HTTP ${response.status})`;
}

// GET {apiBase()}/data/run?metric=&start=&end=&segment= — the exact
// issue-#81 query contract. Never throws: failures return error-as-string.
export async function runMetric(args: RunMetricArgs): Promise<RunMetricResult> {
  const params = new URLSearchParams({
    metric: args.metric,
    start: args.start,
    end: args.end,
    segment: args.segment,
  });
  let response: Response;
  try {
    response = await fetch(`${apiBase()}/data/run?${params.toString()}`);
  } catch {
    return { ok: false, error: "manual run failed: network error" };
  }
  if (!response.ok) {
    return {
      ok: false,
      error: await readError(response, "manual run failed"),
      status: response.status,
    };
  }
  try {
    return { ok: true, data: await response.json() };
  } catch {
    return {
      ok: false,
      error: "manual run failed: invalid response",
      status: response.status,
    };
  }
}

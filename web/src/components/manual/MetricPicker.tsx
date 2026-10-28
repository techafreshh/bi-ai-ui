// Issue #82: the hand-driven path beside the agent chat — metric dropdown,
// Q1–Q4 quarter buttons plus ISO date inputs, segment select, Run button.
// The returned layout renders through the unchanged parseA2UILayout /
// validateComponentProps / registry / A2UIRenderer pipeline (A2UIRenderer
// owns all validation), stamped with a visible `manual` badge and no
// decisions[]. Server 400s degrade to an insight_text card carrying the
// server message verbatim; only transport failures get the inline note —
// never a crash, never a throw.

import { useState } from "react";
import { A2UIRenderer } from "../catalog/A2UIRenderer";
import {
  METRIC_NAMES,
  SEGMENTS,
  quarterBounds,
  runMetric,
} from "../../lib/manual";

// The seeded data window is 2024-10-01→2026-09-30, so quarter buttons
// resolve to calendar year 2026; ISO inputs override any quarter choice.
const DEFAULT_YEAR = 2026;

function errorLayout(message: string): unknown {
  return {
    v: 1,
    layout: "grid",
    items: [
      {
        type: "insight_text",
        props: { title: "Manual run failed", body: message },
      },
    ],
    slotCopy: {},
  };
}

const QUARTERS = [1, 2, 3, 4] as const;

export function MetricPicker() {
  const [metric, setMetric] = useState<string>("nrr");
  const [quarter, setQuarter] = useState<number | null>(3);
  const [start, setStart] = useState<string>("2026-07-01");
  const [end, setEnd] = useState<string>("2026-09-30");
  const [segment, setSegment] = useState<string>("Growth");
  const [result, setResult] = useState<unknown | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);
  const [running, setRunning] = useState<boolean>(false);

  function selectQuarter(next: number): void {
    const [quarterStart, quarterEnd] = quarterBounds(DEFAULT_YEAR, next);
    setQuarter(next);
    setStart(quarterStart);
    setEnd(quarterEnd);
  }

  async function onRun(): Promise<void> {
    setRunning(true);
    try {
      const outcome = await runMetric({ metric, start, end, segment });
      if (outcome.ok) {
        setResult(outcome.data);
        setTransportError(null);
      } else {
        setResult(errorLayout(outcome.error));
        setTransportError(outcome.status === undefined ? outcome.error : null);
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <section
      aria-label="Manual metric run"
      className="mb-6 rounded-xl border border-border bg-card p-4 shadow-sm"
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Metric
          <select
            data-testid="manual-metric"
            value={metric}
            onChange={(event) => setMetric(event.target.value)}
            className="rounded-md border border-border bg-card px-2 py-1 text-sm text-foreground"
          >
            {METRIC_NAMES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          <span id="manual-quarter-label">Quarter</span>
          <div
            role="group"
            aria-labelledby="manual-quarter-label"
            className="flex gap-1"
          >
            {QUARTERS.map((q) => (
              <button
                key={q}
                type="button"
                data-testid={`manual-quarter-q${q}`}
                aria-pressed={quarter === q}
                onClick={() => selectQuarter(q)}
                className="rounded-md border border-border px-2 py-1 text-sm text-foreground aria-pressed:bg-muted"
              >
                Q{q}
              </button>
            ))}
          </div>
        </div>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Start
          <input
            data-testid="manual-start"
            value={start}
            placeholder="YYYY-MM-DD"
            onChange={(event) => {
              setStart(event.target.value);
              setQuarter(null);
            }}
            className="rounded-md border border-border bg-card px-2 py-1 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          End
          <input
            data-testid="manual-end"
            value={end}
            placeholder="YYYY-MM-DD"
            onChange={(event) => {
              setEnd(event.target.value);
              setQuarter(null);
            }}
            className="rounded-md border border-border bg-card px-2 py-1 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Segment
          <select
            data-testid="manual-segment"
            value={segment}
            onChange={(event) => setSegment(event.target.value)}
            className="rounded-md border border-border bg-card px-2 py-1 text-sm text-foreground"
          >
            {SEGMENTS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          data-testid="manual-run"
          disabled={running}
          onClick={() => void onRun()}
          className="rounded-md border border-border bg-primary px-3 py-1 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          Run
        </button>
      </div>
      {result !== null && (
        <div data-testid="manual-result" className="mt-4 flex flex-col gap-2">
          <span
            data-testid="manual-badge"
            className="inline-flex w-fit items-center rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground"
          >
            manual
          </span>
          {transportError !== null && (
            <p
              data-testid="manual-error"
              className="text-xs text-muted-foreground"
            >
              {transportError}
            </p>
          )}
          <A2UIRenderer layout={result} />
        </div>
      )}
    </section>
  );
}

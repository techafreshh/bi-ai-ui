// Issue #12: deterministic chart data for the line_chart catalog component.
//
// The A2UI wire contract carries only {series, range, annotations} — there is
// no data channel in the protocol, so the frontend resolves the series name
// against a local table. `arr_daily` rebuilds the README's planted Q3-2026
// shape (net-new-ARR growth ramp with the Aug-10 churn kink) purely from
// arithmetic — no RNG, no network — so the canvas is reproducible byte for
// byte and real DB data can swap in behind this seam later.

export interface SeriesPoint {
  // ISO calendar day, yyyy-mm-dd.
  date: string;
  value: number;
}

// "2026-07-01..2026-09-30" -> both bounds; null for anything unparseable.
// Both ends are INCLUSIVE (agent/db.py scopes its SQL the same way), so the
// Q3 range spans 92 days — the ≥90-point acceptance floor.
export function parseRange(range: string): { start: string; end: string } | null {
  const match = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/.exec(range.trim());
  if (!match) return null;
  const [, start, end] = match;
  // Reject impossible dates: let the UTC constructor round-trip both bounds
  // and compare — "2026-02-31" normalizes to "2026-03-03" and fails the check.
  for (const bound of [start, end]) {
    if (new Date(`${bound}T00:00:00Z`).toISOString().slice(0, 10) !== bound) {
      return null;
    }
  }
  if (start > end) return null;
  return { start, end };
}

// List the inclusive ISO days between start and end, ascending.
function enumerateDays(start: string, end: string): string[] {
  const days: string[] = [];
  const cursor = new Date(`${start}T00:00:00Z`).getTime();
  const stop = new Date(`${end}T00:00:00Z`).getTime();
  for (let t = cursor; t <= stop; t += 86_400_000) {
    days.push(new Date(t).toISOString().slice(0, 10));
  }
  return days;
}

// arr_daily: daily ARR in dollars over the range.
// - exponential net-new ramp (~0.45% compound/day, the Q3 growth story)
// - mild two-week sine ripple (renewal cadence noise)
// - from Aug 10 onward, a saturating churn drawdown (the planted pricing-
//   change cancellations) that bends the curve but never dominates the ramp.
function arrDaily(date: string, dayIndex: number): number {
  const ramp = 1_900_000 * Math.exp(0.0045 * dayIndex);
  const ripple = 30_000 * Math.sin((2 * Math.PI * dayIndex) / 14);
  const at = new Date(`${date}T00:00:00Z`).getTime();
  const churnStart = new Date("2026-08-10T00:00:00Z").getTime();
  const churn =
    at >= churnStart
      ? 180_000 * (1 - Math.exp(-(at - churnStart) / (20 * 86_400_000)))
      : 0;
  return Math.max(0, Math.round(ramp + ripple - churn));
}

// Named series table — the seam later issues swap for live agent data.
const SERIES_BUILDERS: Record<
  string,
  (date: string, dayIndex: number) => number
> = {
  arr_daily: arrDaily,
};

// Build the points a line_chart should plot. INCLUSIVE day bounds; an unknown
// series name or an unparseable range yields [] (the component renders its
// own "no data" state — this module never throws).
export function buildSeries(series: string, range: string): SeriesPoint[] {
  const parsed = parseRange(range);
  const builder = Object.hasOwn(SERIES_BUILDERS, series)
    ? SERIES_BUILDERS[series]
    : undefined;
  if (!parsed || !builder) return [];
  const days = enumerateDays(parsed.start, parsed.end);
  return days.map((date, dayIndex) => ({
    date,
    value: builder(date, dayIndex),
  }));
}

// Issue #14: browser-side mirror of the decide node's shared state field
// `decisions[]` (agent/graph.py decide(): one entry per answer carrying
// {id, type, probabilities, pick, model, latency_ms, above_threshold}). Same
// mirror-the-server-contract convention as lib/a2ui.ts, but deliberately NOT
// a .strict() Zod schema: the acceptance contract requires a record missing
// `latency_ms` to render as "n/a", and a strict schema would drop exactly
// those records. Hand-rolled tolerant guards parse instead — whatever
// survives extraction renders, the rest is skipped, nothing is thrown.

// Keys verbatim from agent/graph.py's decide() output. `probabilities` is a
// scalar in [0, 1] for score answers (score questions read a confidence), a
// scalar ALSO for a raw noul answer, or an {option: probability} dict for
// choice answers — and, since issue #16, the real-change noul is re-stamped
// server-side into the {real, data_problem} dict (summing to 1), so a noul
// record renders one bar per side. In the wire contract the parser only
// *enforces* id/type; every other field is lenient so a partial record still
// renders (missing latency_ms -> "n/a", missing model -> "n/a", missing pick
// -> "picked: n/a").
export interface DecisionRecord {
  id: string;
  type: string;
  probabilities: Record<string, number> | number;
  pick?: string | number;
  model?: string;
  latency_ms?: number;
  above_threshold?: boolean;
}

// One rendered probability bar: `label` is shown next to the bar, `value`
// is a finite number the drawer formats as a percentage and bar width.
export interface DecisionProbabilityRow {
  label: string;
  value: number;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Extract the decisions array from an AG-UI state snapshot. Tolerant by
// design: a non-object state, an inherited `decisions` key, a non-array
// value, or an entry without a non-empty string id and a string type all
// yield [] / get skipped instead of crashing the stream.
export function decisionsFromState(state: unknown): DecisionRecord[] {
  if (!isPlainObject(state)) return [];
  // Own-property-only lookup (same hazard as the component registry): a
  // plain-object map resolves "toString"/"constructor"/"__proto__" off
  // Object.prototype, and Object.hasOwn rejects that whole inherited class —
  // prototype-chain members must yield [], never parsed decisions.
  if (!Object.hasOwn(state, "decisions")) return [];
  const raw = state.decisions;
  if (!Array.isArray(raw)) return [];
  const records: DecisionRecord[] = [];
  for (const entry of raw) {
    if (!isPlainObject(entry)) continue;
    if (typeof entry.id !== "string" || entry.id === "") continue;
    if (typeof entry.type !== "string") continue;
    // Verbatim passthrough: the drawer renders id/type/probabilities/pick/
    // model/latency_ms off this shape and never rewrites a value — truthful
    // attribution is the agent's contract, not the drawer's to fix.
    records.push(entry as unknown as DecisionRecord);
  }
  return records;
}

// Tolerant probability rows for one record: scalar -> a single labeled row
// ("yes" for noul, "score" for score, "p" otherwise), dict -> one row per
// own-property option key with a finite number value (0 stays — a 0%
// probability is a real bar). Garbage in (missing/malformed probabilities)
// yields zero bars instead of crashing.
export function recordProbabilities(
  record: DecisionRecord,
): DecisionProbabilityRow[] {
  const { probabilities } = record;
  if (typeof probabilities === "number") {
    if (!Number.isFinite(probabilities) || typeof record.type !== "string") {
      return [];
    }
    const label =
      record.type === "noul" ? "yes" : record.type === "score" ? "score" : "p";
    return [{ label, value: probabilities }];
  }
  if (!isPlainObject(probabilities)) return [];
  const rows: DecisionProbabilityRow[] = [];
  for (const [option, value] of Object.entries(probabilities)) {
    if (typeof value === "number" && Number.isFinite(value)) {
      rows.push({ label: option, value });
    }
  }
  return rows;
}

// Equality over two decisions arrays by JSON text — the accumulation hook
// skips re-broadcasts of an array it has already merged. Byte-sensitive on
// purpose: only a byte-identical re-broadcast of the same state snapshot is
// a duplicate; a genuinely new run produces a different array (latency
// floats differ) and must accumulate.
export function sameDecisions(
  a: DecisionRecord[],
  b: DecisionRecord[],
): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// 0.902 -> "90%": integer-rounded percentage string for the bar label.
export function formatPercent(p: number): string {
  return `${Math.round(p * 100)}%`;
}

// Latency display: milliseconds rounded to whole ms with an explicit unit
// ("123 ms"). An absent latency (null/undefined — and a non-finite sentinel,
// which can only be transport garbage) renders "n/a" instead of crashing.
// 0 is a real latency and stays "0 ms".
export function formatLatency(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "n/a";
  return `${Math.round(ms)} ms`;
}

// Bar width for a probability, clamped to [0, 100]%: an out-of-range value
// from a misbehaving transport can never blow the fill outside its track.
export function barWidthPercent(p: number): string {
  const width = Math.min(100, Math.max(0, Math.round(p * 100)));
  return `${width}%`;
}

// Issue #87: flat probability text for the simplified decision-drawer row.
// A finite number renders verbatim ("0.9"), anything else renders "n/a"
// so a garbage scalar never crashes the row — the row still contains the
// literal "p=" prefix the control-layer probes match on.
export function formatProbability(p: number): string {
  if (typeof p !== "number" || !Number.isFinite(p)) return "n/a";
  return String(p);
}

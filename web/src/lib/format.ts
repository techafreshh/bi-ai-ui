// Issue #12: display formatting for the catalog components. Plain exported
// helpers in the same style as lib/decisions.ts' formatPercent/formatLatency —
// pure, deterministic, colocated tests. Currency format contract (acceptance):
// values ≥ $1M render as "$1.20M", values ≥ $1K as "$24.5K", smaller values
// with full comma-grouped dollars; the $ and the M/K suffix are the pinned
// pieces the black-box probes match on.

function signed(value: number): { sign: string; abs: number } {
  const sign = value < 0 ? "-" : "";
  return { sign, abs: Math.abs(value) };
}

// 1_200_000 -> "$1.20M"; 24_500 -> "$24.5K"; 57_000 -> "$57,000";
// -50_000 -> "-$50.0K". Non-finite input degrades to "$—" (never "NaN").
export function formatCurrency(value: number): string {
  if (!Number.isFinite(value)) return "$—";
  const { sign, abs } = signed(value);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${Math.round(abs).toLocaleString("en-US")}`;
}

// Ratio deltas render as whole percents with an explicit sign on gains:
// 0.18 -> "+18%"; -0.03 -> "-3%"; 0 -> "0%". Non-finite degrades to "—".
export function formatDelta(delta: number): string {
  if (!Number.isFinite(delta)) return "—";
  const percent = Math.round(delta * 100);
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent}%`;
}

// Data-quality confidence scores are floats in [0, 1] (agent/quality.py);
// 0.95 -> "95%". Non-finite degrades to "—".
export function formatScore(score: number): string {
  if (!Number.isFinite(score)) return "—";
  return `${Math.round(score * 100)}%`;
}

// Wire keys are snake_case agent-side ("account", "mrr", "nulls"); headers
// deserve title case, with the domain's known acronyms kept uppercase.
const ACRONYMS = new Set(["arr", "mrr", "cac", "ltv", "id", "sku"]);

export function humanizeKey(key: string): string {
  return key
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((word) =>
      ACRONYMS.has(word.toLowerCase())
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(" ");
}

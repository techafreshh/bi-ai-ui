/**
 * DecisionDrawer: every probabilistic decision behind the current view —
 * question, options with probability bars, pick, model, latency (PRD §4
 * "radically observable"). Drove-off state: the agent snapshots carry the
 * full decisions list on every node, so the drawer mirrors the live run.
 */
import type { DecisionRecord } from "../lib/a2ui";

const TYPE_BADGES: Record<string, string> = {
  choice: "bg-indigo-100 text-indigo-800",
  noul: "bg-purple-100 text-purple-800",
  score: "bg-teal-100 text-teal-800",
};

function pickLabel(decision: DecisionRecord): string {
  if (Array.isArray(decision.pick)) {
    return decision.pick.length > 0 ? decision.pick.join(", ") : "none above threshold";
  }
  if (typeof decision.pick === "string") {
    return decision.pick;
  }
  if (decision.type === "score" && typeof decision.value === "number") {
    return `confidence ${decision.value.toFixed(2)}`;
  }
  return "—";
}

function ProbabilityBars({ decision }: { decision: DecisionRecord }) {
  if (decision.type === "choice") {
    const entries = Object.entries(decision.probabilities).sort((a, b) => b[1] - a[1]);
    const picked = new Set(Array.isArray(decision.pick) ? decision.pick : []);
    return (
      <ul className="space-y-1.5">
        {entries.map(([option, p]) => (
          <li key={option} className="flex items-center gap-2">
            <span
              className={`w-36 shrink-0 truncate text-xs ${
                picked.has(option) ? "font-semibold text-slate-900" : "text-slate-500"
              }`}
              title={option}
            >
              {option}
            </span>
            <span className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
              <span
                className={`block h-full rounded-full ${
                  picked.has(option) ? "bg-slate-900" : "bg-slate-400"
                }`}
                style={{ width: `${Math.round(p * 100)}%` }}
              />
            </span>
            <span className="w-10 text-right text-xs tabular-nums text-slate-600">
              {p.toFixed(2)}
            </span>
          </li>
        ))}
      </ul>
    );
  }
  const p = decision.probabilities.p ?? 0;
  return (
    <div className="flex items-center gap-2">
      <span className="h-2 w-40 overflow-hidden rounded-full bg-slate-100">
        <span
          className={`block h-full rounded-full ${decision.passed ? "bg-purple-600" : "bg-slate-400"}`}
          style={{ width: `${Math.round(p * 100)}%` }}
        />
      </span>
      <span className="text-xs tabular-nums text-slate-600">p = {p.toFixed(2)}</span>
      {typeof decision.value === "number" ? (
        <span className="text-xs tabular-nums text-slate-400">evidence {decision.value}</span>
      ) : null}
    </div>
  );
}

export default function DecisionDrawer({
  open,
  onClose,
  decisions,
}: {
  open: boolean;
  onClose: () => void;
  decisions: DecisionRecord[];
}) {
  return (
    <div
      className={`fixed inset-0 z-40 ${open ? "" : "pointer-events-none"}`}
      aria-hidden={!open}
    >
      <div
        className={`absolute inset-0 bg-slate-900/30 transition-opacity ${
          open ? "opacity-100" : "opacity-0"
        }`}
        onClick={onClose}
      />
      <aside
        className={`absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-white shadow-xl transition-transform ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Decision log</h2>
            <p className="text-xs text-slate-500">
              {decisions.length} typed decisions · question → probabilities → pick → model
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm text-slate-500 hover:bg-slate-100"
          >
            Close
          </button>
        </header>
        <div className="flex-1 space-y-3 overflow-auto p-4">
          {decisions.length === 0 ? (
            <p className="text-sm text-slate-500">
              Send a query — every decision the agent makes shows up here.
            </p>
          ) : (
            decisions.map((decision) => (
              <article
                key={decision.id}
                className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                      TYPE_BADGES[decision.type] ?? "bg-slate-100 text-slate-700"
                    }`}
                  >
                    {decision.type}
                  </span>
                  <h3 className="text-xs font-medium text-slate-700">{decision.question}</h3>
                </div>
                <div className="mt-2">
                  <ProbabilityBars decision={decision} />
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">
                    pick: {pickLabel(decision)}
                  </span>
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-600">
                    {decision.model}
                  </span>
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 tabular-nums text-slate-600">
                    {decision.latency_ms.toFixed(1)} ms
                  </span>
                  <span
                    className={`rounded px-1.5 py-0.5 font-medium ${
                      decision.passed
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-rose-100 text-rose-700"
                    }`}
                  >
                    {decision.passed ? "passed" : "below threshold"}
                  </span>
                </div>
              </article>
            ))
          )}
        </div>
      </aside>
    </div>
  );
}

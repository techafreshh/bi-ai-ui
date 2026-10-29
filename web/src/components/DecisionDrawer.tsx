// Issue #14: the decision-log drawer — radical observability means every
// decision is inspectable with probabilities, model, and latency. Purely
// presentational: it owns only its open/closed state and renders the
// accumulated `decisions[]` it is given verbatim (model and latency are the
// agent's truthful attribution — never substituted or prettified here).
// Issue #87: simplified to a flat per-decision text row containing the
// literal substrings `p=`, `model`, and `latency` so the control layer is
// observable as plain text instead of the bar-grid presentation.

import { useState } from "react";
import {
  formatLatency,
  formatProbability,
  recordProbabilities,
  type DecisionRecord,
} from "../lib/decisions";

// One flat probability summary per record, always prefixed with the literal
// `p=` the probes match on. Scalars render as `p=<scalar>`; dicts render
// as `p=<option>=<value>, ...`; garbage renders as `p=n/a` — never a crash.
function probabilityText(record: DecisionRecord): string {
  const { probabilities } = record;
  if (typeof probabilities === "number") {
    return `p=${formatProbability(probabilities)}`;
  }
  const rows = recordProbabilities(record);
  if (rows.length === 0) return "p=n/a";
  return `p=${rows.map((row) => `${row.label}=${formatProbability(row.value)}`).join(", ")}`;
}

export function DecisionDrawer({ decisions }: { decisions: DecisionRecord[] }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        data-testid="decision-log-button"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium text-foreground shadow-sm hover:bg-muted"
      >
        Decision log
      </button>
      {open && (
        <aside
          data-testid="decision-drawer"
          role="dialog"
          aria-label="Decision log"
          className="fixed inset-y-0 right-0 z-50 flex w-[420px] max-w-full flex-col border-l border-border bg-card shadow-xl"
        >
          <header className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">Decision log ({decisions.length})</h2>
            <button
              type="button"
              data-testid="decision-drawer-close"
              aria-label="Close decision log"
              onClick={() => setOpen(false)}
              className="rounded-md border border-border px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted"
            >
              Close
            </button>
          </header>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {decisions.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                no dataset — send a query first.
              </p>
            ) : (
              <div className="space-y-3">
                {decisions.map((record, index) => {
                  return (
                    <article
                      // ids recur across runs, so the index is part of the
                      // identity: one article per streamed decision, union
                      // duplicates included.
                      key={`${record.id}-${index}`}
                      data-testid="decision-entry"
                      className="rounded-lg border border-border bg-muted p-3"
                    >
                      <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-foreground">
                        <span className="truncate">#{record.id}</span>
                        <span className="shrink-0 rounded bg-border-strong px-1.5 py-0.5">
                          {record.type}
                        </span>
                      </div>
                      <p className="text-xs text-foreground">
                        {probabilityText(record)} model{" "}
                        <span
                          data-testid="decision-model"
                          className="min-w-0 truncate"
                        >
                          {String(record.model ?? "n/a")}
                        </span>{" "}
                        latency{" "}
                        <span
                          data-testid="decision-latency"
                          className="shrink-0 tabular-nums"
                        >
                          {formatLatency(record.latency_ms)}
                        </span>
                      </p>
                      <p className="mt-2 text-xs text-muted-foreground">
                        picked: {String(record.pick ?? "n/a")}
                      </p>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </aside>
      )}
    </>
  );
}

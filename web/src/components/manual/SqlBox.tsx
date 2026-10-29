// Issue #84: the hand-driven SQL box beside the agent chat — a textarea
// plus Run posting to the guarded POST /data/query API and rendering
// results through the unchanged A2UIRenderer pipeline. Success composes a
// data_table (columns = cursor keys, list rows mapped to dicts in
// lib/manual.ts); server 400s degrade to an insight_text card carrying the
// server message verbatim; only transport failures get the inline note —
// never a crash, never a throw, never a stack trace.

import { useState } from "react";
import { A2UIRenderer } from "../catalog/A2UIRenderer";
import { runSql } from "../../lib/manual";

function errorLayout(message: string): unknown {
  return {
    v: 1,
    layout: "grid",
    items: [
      {
        type: "insight_text",
        props: { title: "SQL run failed", body: message },
      },
    ],
    slotCopy: {},
  };
}

export function SqlBox() {
  const [sql, setSql] = useState<string>(
    "SELECT segment, COUNT(*) AS n FROM accounts GROUP BY segment",
  );
  const [result, setResult] = useState<unknown | null>(null);
  const [failed, setFailed] = useState<boolean>(false);
  const [transportError, setTransportError] = useState<string | null>(null);
  const [running, setRunning] = useState<boolean>(false);

  async function onRun(): Promise<void> {
    setRunning(true);
    try {
      const outcome = await runSql(sql);
      if (outcome.ok) {
        setResult({
          v: 1,
          layout: "grid",
          items: [
            {
              type: "data_table",
              props: { columns: outcome.columns, rows: outcome.rows },
            },
          ],
          slotCopy: {},
        });
        setFailed(false);
        setTransportError(null);
      } else {
        setResult(errorLayout(outcome.error));
        setFailed(true);
        setTransportError(outcome.status === undefined ? outcome.error : null);
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <section
      aria-label="Manual SQL run"
      className="mb-6 rounded-xl border border-border bg-card p-4 shadow-sm"
    >
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          SQL
          <textarea
            data-testid="sql-input"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            rows={3}
            spellCheck={false}
            className="rounded-md border border-border bg-card px-2 py-1 font-mono text-sm text-foreground"
          />
        </label>
        <div>
          <button
            type="button"
            data-testid="sql-run"
            disabled={running}
            onClick={() => void onRun()}
            className="rounded-md border border-border bg-primary px-3 py-1 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            Run
          </button>
        </div>
      </div>
      {result !== null && (
        <div data-testid="sql-result" className="mt-4 flex flex-col gap-2">
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
          {failed ? (
            <div data-testid="insight-text">
              <A2UIRenderer layout={result} />
            </div>
          ) : (
            <A2UIRenderer layout={result} />
          )}
        </div>
      )}
    </section>
  );
}

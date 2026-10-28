// Issue #70: the honest empty state. A fresh clone boots with no dataset,
// so the canvas defaults to this static text instead of a demo fixture —
// the exact contract string the acceptance probe greps for on `/`.
//
// Issue #76: the empty canvas owns the under-a-minute load loop — a file
// dropzone (single CSV, table inferred from the filename stem) plus the
// one-click `Load sample` path that activates the server-side `SAMPLES_DIR`
// miniature. Both paths accept an optional `onLoaded` callback the App shell
// uses to refresh the header picker.

import { useState } from "react";
import { activateDataset, loadSample, uploadCsv } from "../../lib/datasets";

const ALLOWED_TABLES = [
  "accounts",
  "subscription_events",
  "revenue_events",
  "usage_events",
] as const;

function tableForFilename(filename: string): string {
  const stem = filename.replace(/\.[^.]*$/, "");
  return stem;
}

export function EmptyState({ onLoaded }: { onLoaded?: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleFile(file: File) {
    const table = tableForFilename(file.name);
    if (!(ALLOWED_TABLES as readonly string[]).includes(table)) {
      setError(
        `unknown table ${JSON.stringify(table)}; must be one of ${ALLOWED_TABLES.join(", ")}`,
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const uploaded = await uploadCsv(file, table);
      await activateDataset(uploaded.id);
      onLoaded?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleLoadSample() {
    setBusy(true);
    setError(null);
    try {
      await loadSample();
      onLoaded?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "failed to load sample");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      data-testid="empty-state"
      className="mx-auto flex w-full max-w-6xl flex-col items-center gap-2 py-16 text-center"
    >
      <p className="text-lg font-medium text-foreground">
        No dataset loaded — upload CSV or load sample
      </p>
      <p className="text-sm text-muted-foreground">
        Boot stays green with no dataset; add data to see the dashboard.
      </p>
      <div
        data-testid="upload-dropzone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const file = event.dataTransfer.files?.[0];
          if (file !== undefined) {
            void handleFile(file);
          }
        }}
        className="mt-4 w-full max-w-md rounded-lg border border-dashed border-border p-6"
      >
        <p className="text-sm text-muted-foreground">
          Drag a CSV here or choose a file
        </p>
        <input
          data-testid="upload-input"
          type="file"
          accept=".csv"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file !== undefined) {
              void handleFile(file);
            }
          }}
          className="mt-2 text-sm"
        />
      </div>
      {error !== null && (
        <p data-testid="upload-error" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <button
        type="button"
        data-testid="load-sample"
        disabled={busy}
        onClick={() => void handleLoadSample()}
        className="mt-2 rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium text-foreground shadow-sm hover:bg-muted"
      >
        Load sample
      </button>
    </section>
  );
}

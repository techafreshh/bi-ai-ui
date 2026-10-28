// Issue #76: header dataset picker — shows which dataset is active with its
// profile line (`source rows data_end`) and switches the active id. Purely
// presentational apart from the fetch-on-mount/refetch cycle; fetch failures
// render a muted inline note, never a crash.

import { useCallback, useEffect, useState } from "react";
import {
  activateDataset,
  listDatasets,
  type DatasetInfo,
  type DatasetList,
} from "../../lib/datasets";

export function DatasetPicker({ refreshKey }: { refreshKey?: number }) {
  const [listing, setListing] = useState<DatasetList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setListing(await listDatasets());
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "failed to list datasets");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  const datasets: DatasetInfo[] = listing?.datasets ?? [];
  const activeId: string | null = listing?.active_id ?? null;
  const active: DatasetInfo | undefined = datasets.find((d) => d.id === activeId);

  async function onSelectChange(nextId: string) {
    if (!nextId) {
      return;
    }
    try {
      await activateDataset(nextId);
      await refresh();
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "failed to activate dataset");
    }
  }

  return (
    <div className="flex items-center gap-2">
      <label
        htmlFor="dataset-select"
        className="text-xs font-medium text-muted-foreground"
      >
        Dataset
      </label>
      <select
        id="dataset-select"
        data-testid="dataset-select"
        value={activeId ?? ""}
        onChange={(event) => void onSelectChange(event.target.value)}
        className="rounded-md border border-border bg-card px-2 py-1 text-sm text-foreground"
      >
        <option value="">No dataset</option>
        {datasets.map((dataset) => (
          <option key={dataset.id} value={dataset.id}>
            {dataset.id}
          </option>
        ))}
      </select>
      {active !== undefined && (
        <p
          data-testid="dataset-profile"
          className="text-xs text-muted-foreground"
        >{`${active.source} ${active.rows} rows ${active.data_end ?? ""}`}</p>
      )}
      {error !== null && (
        <p className="text-xs text-muted-foreground">{error}</p>
      )}
    </div>
  );
}

// Issue #76: thin browser REST client for the dataset loop. The REST base
// derives from the single agent identity in lib/agent.ts (strip the trailing
// `/copilotkit` AG-UI suffix) so remapped Docker ports keep working with
// zero new env vars — no second VITE_* URL that could drift from AGENT_URL.

import { AGENT_URL } from "./agent";

export interface DatasetInfo {
  id: string;
  source: string;
  tables: string[];
  rows: number;
  data_end: string | null;
}

export interface DatasetList {
  active_id: string | null;
  datasets: DatasetInfo[];
}

export interface UploadResult {
  id: string;
  table: string;
  rows: number;
  data_end: string | null;
}

export interface SampleResult {
  active_id: string;
  tables: string[];
  rows: number;
  data_end: string | null;
}

export function apiBase(): string {
  const raw = (AGENT_URL ?? "").trim().replace(/\/+$/, "");
  const suffix = "/copilotkit";
  const withoutSuffix = raw.toLowerCase().endsWith(suffix)
    ? raw.slice(0, raw.length - suffix.length)
    : raw;
  return withoutSuffix.replace(/\/+$/, "");
}

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

export async function listDatasets(): Promise<DatasetList> {
  const response = await fetch(`${apiBase()}/data/datasets`);
  if (!response.ok) {
    throw new Error(await readError(response, "failed to list datasets"));
  }
  try {
    return (await response.json()) as DatasetList;
  } catch {
    throw new Error("failed to list datasets: invalid response");
  }
}

export async function activateDataset(id: string): Promise<string> {
  const response = await fetch(`${apiBase()}/data/active`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!response.ok) {
    throw new Error(await readError(response, "failed to activate dataset"));
  }
  try {
    const payload = (await response.json()) as { active_id: string };
    return payload.active_id;
  } catch {
    throw new Error("failed to activate dataset: invalid response");
  }
}

export async function uploadCsv(file: File, table: string): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("table", table);
  const response = await fetch(`${apiBase()}/data/upload`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error(await readError(response, "upload failed"));
  }
  try {
    return (await response.json()) as UploadResult;
  } catch {
    throw new Error("upload failed: invalid response");
  }
}

export async function loadSample(): Promise<SampleResult> {
  const response = await fetch(`${apiBase()}/data/sample`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await readError(response, "failed to load sample"));
  }
  try {
    return (await response.json()) as SampleResult;
  } catch {
    throw new Error("failed to load sample: invalid response");
  }
}

/**
 * Zod mirror of the agent's A2UI layout envelope (agent/catalog.py is the
 * Pydantic side). The canvas re-validates every snapshot before rendering:
 * an unparseable payload falls back to a safe view, and an item whose type is
 * not in the shared catalog renders the per-item fallback card (PRD §10
 * "unknown type or invalid payload → fallback").
 */
import { z } from "zod";

export const A2UI_ITEM_TYPES = [
  "kpi_row",
  "line_chart",
  "variance_callout",
  "data_table",
  "data_quality_card",
  "insight_text",
] as const;

export type A2UIItemType = (typeof A2UI_ITEM_TYPES)[number];

// Item type is a plain string: unknown types stay parseable (so the rest of
// the grid keeps rendering) and the registry maps them to FallbackCard.
const itemSchema = z.object({
  type: z.string(),
  props: z.record(z.unknown()).default({}),
});

export const a2uiSchema = z.object({
  v: z.number().default(1),
  layout: z.string().default("grid"),
  route: z.string(),
  items: z.array(itemSchema),
  slotCopy: z.record(z.string()).optional(),
});

export type A2UIItem = z.infer<typeof itemSchema>;
export type A2UILayout = z.infer<typeof a2uiSchema>;

export type DecisionRecord = {
  id: string;
  type: "choice" | "noul" | "score" | string;
  question: string;
  options: string[];
  probabilities: Record<string, number>;
  pick: string[] | string | null;
  value?: number | null;
  threshold: number;
  model: string;
  latency_ms: number;
  passed: boolean;
};

export type DemoStatus = {
  stage?: string;
  route?: string;
  mode?: string;
  decision_model?: string | null;
  query?: string;
  fallback?: boolean;
  picked_components?: string[];
  dropped?: string[];
  warnings?: string[];
};

/** Shape of the full state triple the agent snapshots on every node. */
export type AgentUiState = {
  a2ui: A2UILayout | null;
  decisions: DecisionRecord[];
  status: DemoStatus;
};

export type LayoutValidation =
  | { layout: A2UILayout; error?: undefined; fallback?: undefined }
  | { layout?: undefined; error: string; fallback: true };

export function validateLayout(raw: unknown): LayoutValidation {
  if (raw === null || raw === undefined) {
    return { layout: undefined, error: "no layout", fallback: true };
  }
  const parsed = a2uiSchema.safeParse(raw);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return {
      layout: undefined,
      error: issue ? `${issue.path.join(".")}: ${issue.message}` : "invalid layout",
      fallback: true,
    };
  }
  return { layout: parsed.data };
}

/**
 * Extract the {a2ui, decisions, status} triple from a wire state snapshot.
 * Keys are tracked independently (snapshots replace the whole state object,
 * so consumers must not assume every key is present in every snapshot).
 */
export function extractAgentUiState(raw: unknown): AgentUiState {
  const source = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;

  const a2uiParsed =
    "a2ui" in source && source.a2ui !== null && source.a2ui !== undefined
      ? validateLayout(source.a2ui)
      : undefined;
  const a2ui = a2uiParsed?.layout ?? null;

  const decisions: DecisionRecord[] = Array.isArray(source.decisions)
    ? source.decisions.filter(
        (d): d is DecisionRecord =>
          typeof d === "object" && d !== null &&
          typeof (d as DecisionRecord).id === "string" &&
          typeof (d as DecisionRecord).model === "string",
      )
    : [];

  const status: DemoStatus =
    typeof source.status === "object" && source.status !== null ? (source.status as DemoStatus) : {};

  return { a2ui, decisions, status };
}

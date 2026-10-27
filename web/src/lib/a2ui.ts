import { z } from "zod";

// PRD §10 / issue #11: the frontend re-validates every layout the agent
// streams before rendering it — an unknown type or an invalid payload
// degrades to a fallback card, never a React error. This module is the
// browser-side mirror of the server contract in agent/catalog.py
// (A2UILayout + the fixed 6-component catalog, PRD §7).

// The frozen catalog, in the same fixed order as catalog.py's discriminated
// union (quality.py's _CHECK_NAMES idiom): everything below — the prop
// schema map keys, the registry keys, the contract test — derives from it.
export const COMPONENT_TYPES = [
  "kpi_row",
  "line_chart",
  "variance_callout",
  "data_table",
  "data_quality_card",
  "insight_text",
] as const;

export type A2UIComponentType = (typeof COMPONENT_TYPES)[number];

// --- Per-type prop schemas (field names verbatim from agent/catalog.py) ----

// One KPI tile: {label, value, delta} (catalog.KpiEntry). value is int|float
// server-side; JSON has no int distinction, so z.number() covers both.
export const kpiEntrySchema = z
  .object({
    label: z.string(),
    value: z.number(),
    delta: z.number(),
  })
  .strict();

// One data-quality check row: exactly the {name, status, score} shape
// agent/quality.py's run_checks returns (catalog.CheckResult); score is the
// confidence the data is CLEAN, a float in [0, 1] by quality.py's contract,
// so the mirror enforces the range even though catalog.py types it plain.
export const dataQualityCheckSchema = z
  .object({
    name: z.string(),
    status: z.enum(["ok", "warn", "fail"]),
    score: z.number().min(0).max(1),
  })
  .strict();

// One schema per catalog type, keyed in COMPONENT_TYPES order. Every schema
// is .strict() to mirror catalog.py's extra="forbid": in a fixed 6-component
// catalog an unrecognized key can only be a bug or an injection attempt.
export const componentPropsSchemas: Record<A2UIComponentType, z.ZodType> = {
  // Deliberately UNBOUNDED length: catalog.py enforces PRD §7's "3–4 KPIs",
  // but the frontend mirror must accept the PRD §10 wire example itself,
  // which carries a single KPI tile.
  kpi_row: z.object({ kpis: z.array(kpiEntrySchema) }).strict(),
  line_chart: z
    .object({
      series: z.string(),
      range: z.string(),
      // catalog.LineChartProps: annotations: list[str] = [] — optional with
      // a default, so the mirror defaults too (absent → []).
      annotations: z.array(z.string()).default([]),
    })
    .strict(),
  // catalog.VarianceCalloutProps: metric + delta required, baseline / cause /
  // cta optional str | None — a callout without a delta is meaningless.
  variance_callout: z
    .object({
      metric: z.string(),
      delta: z.number(),
      baseline: z.string().nullish(),
      cause: z.string().nullish(),
      cta: z.string().nullish(),
    })
    .strict(),
  // catalog.DataTableProps: columns (strings) + rows (generic dicts, ≤50 per
  // PRD §7) required, filters optional list[str]. Row objects stay open
  // (dict[str, Any] server-side) — only their object shape is pinned.
  data_table: z
    .object({
      columns: z.array(z.string()),
      rows: z.array(z.record(z.string(), z.unknown())).max(50),
      filters: z.array(z.string()).default([]),
    })
    .strict(),
  // catalog.DataQualityCardProps: checks required; issue #17 adds the
  // optional agent-stamped data_problem (the banner gate p, str|None
  // counterpart below) and data_problem_detail (the narrated evidence
  // line) — absent on fixture/older renders, so nullish like the callout's
  // optional slots.
  data_quality_card: z
    .object({
      checks: z.array(dataQualityCheckSchema),
      data_problem: z.number().nullish(),
      data_problem_detail: z.string().nullish(),
    })
    .strict(),
  insight_text: z.object({ title: z.string(), body: z.string() }).strict(),
};

// --- Wire format ------------------------------------------------------------

// Permissive item envelope: unlike catalog.py's closed discriminated union,
// an unknown "type" must SURVIVE envelope validation and reach the renderer,
// which degrades it per item to an "Unknown component" fallback card
// (issue #11 acceptance) instead of failing the whole layout. Extra wrapper
// keys are stripped rather than rejected for the same reason.
export const a2UIItemSchema = z.object({
  type: z.string().min(1),
  props: z.record(z.string(), z.unknown()).optional(),
});

// PRD §10 wire format, mirroring catalog.A2UILayout exactly: v is guarded to
// literal 1 and layout to literal "grid" (the only protocol version defined —
// a draft claiming v=2 must fail here, not silently render), slotCopy is
// {slot -> copy} string record defaulting to {} like the Pydantic model.
export const a2UILayoutSchema = z
  .object({
    v: z.literal(1),
    layout: z.literal("grid"),
    items: z.array(a2UIItemSchema),
    slotCopy: z.record(z.string(), z.string()).default({}),
  })
  .strict();

// --- Parse results -----------------------------------------------------------

// Discriminated result instead of throwing: every caller (renderer, canvas,
// tests) gets one shape to branch on — {ok: true, data} | {ok: false, error}.
export type ParseResult<T> = { ok: true; data: T } | { ok: false; error: string };

export type A2UIItem = z.infer<typeof a2UIItemSchema>;
export type A2UILayout = z.infer<typeof a2UILayoutSchema>;

function formatZodError(error: z.ZodError): string {
  const first = error.issues[0];
  const path = first.path.length > 0 ? first.path.join(".") : "(root)";
  const extra =
    error.issues.length > 1 ? ` (+${error.issues.length - 1} more)` : "";
  return `${path}: ${first.message}${extra}`;
}

// Validate a full layout payload (PRD §10 envelope + items). Intentionally
// does NOT check per-type props: item-level validation happens per type in
// validateComponentProps so unknown types degrade per item, not per layout.
export function parseA2UILayout(payload: unknown): ParseResult<A2UILayout> {
  const result = a2UILayoutSchema.safeParse(payload);
  if (result.success) return { ok: true, data: result.data };
  return { ok: false, error: formatZodError(result.error) };
}

// Validate one item's props against its catalog type's schema. The renderer
// only calls this for types present in the registry; an off-catalog type
// has no schema and fails closed.
export function validateComponentProps(
  type: string,
  props: unknown,
): ParseResult<unknown> {
  // Own-property-only lookup (same hazard as the registry): a plain-object
  // map resolves "toString"/"constructor"/"__proto__" off Object.prototype,
  // and a truthy non-schema there would throw schema.safeParse instead of
  // failing closed. Object.hasOwn rejects the whole inherited class.
  const schema: z.ZodType | undefined = Object.hasOwn(componentPropsSchemas, type)
    ? componentPropsSchemas[type as A2UIComponentType]
    : undefined;
  if (!schema) {
    return { ok: false, error: `no schema for component type "${type}"` };
  }
  const result = schema.safeParse(props);
  if (result.success) return { ok: true, data: result.data };
  return { ok: false, error: formatZodError(result.error) };
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Extract the PRD §10 layout from an AG-UI state snapshot (issue #17).
// Tolerant like decisionsFromState: a non-object state, an inherited `a2ui`
// key, or a payload failing parseA2UILayout all yield null — the consuming
// hook keeps the previous layout instead of breaking the render mid-run.
export function a2uiFromState(state: unknown): A2UILayout | null {
  if (!isPlainObject(state)) return null;
  // Own-property-only lookup (same hazard as the schema map above):
  // prototype-chain members must yield null, never a parsed layout.
  if (!Object.hasOwn(state, "a2ui")) return null;
  const parsed = parseA2UILayout(state.a2ui);
  return parsed.ok ? parsed.data : null;
}

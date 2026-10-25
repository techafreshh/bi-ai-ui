/**
 * Canvas: the composed dashboard. Validates state.a2ui on every render
 * (fallback view on invalid payload), renders skeleton states while props
 * are empty, banners the DQ warnings, and shows assembled-component pills.
 */
import { componentFor } from "./catalog/registry";
import { Card } from "./catalog/shared";
import { validateLayout, type DecisionRecord, type DemoStatus } from "../lib/a2ui";

const COMPONENT_PILLS: Record<string, string> = {
  kpi_row: "KPI row",
  line_chart: "Line chart",
  variance_callout: "Variance callout",
  data_table: "Data table",
  data_quality_card: "Data quality card",
  insight_text: "Insight text",
};

const STAGE_LABELS: Record<string, string> = {
  reading: "Routing query…",
  deciding: "Deciding components…",
  layout: "Composing layout…",
  querying: "Running queries…",
  done: "Ready",
};

export interface CanvasProps {
  a2ui: unknown;
  decisions: DecisionRecord[];
  status: DemoStatus;
  onAction: (text: string) => void;
}

export default function Canvas({ a2ui, decisions, status, onAction }: CanvasProps) {
  if (a2ui === null || a2ui === undefined) {
    return (
      <div className="flex h-full items-center justify-center px-6">
        <div className="max-w-md text-center">
          <p className="text-lg font-semibold text-slate-700">
            Ask the agent — the dashboard assembles itself
          </p>
          <p className="mt-2 text-sm text-slate-500">
            The agent classifies your question, picks components from a fixed
            catalog, queries the demo warehouse, then writes the narrative.
            Every probabilistic decision is inspectable in the Decision log.
          </p>
        </div>
      </div>
    );
  }

  const validation = validateLayout(a2ui);
  if (!validation.layout) {
    return (
      <div className="p-6">
        <Card className="border-amber-200 bg-amber-50">
          <p className="text-sm font-medium text-amber-800">
            Layout failed validation and was not rendered.
          </p>
          <p className="mt-1 font-mono text-xs text-amber-700">{validation.error}</p>
        </Card>
      </div>
    );
  }

  const layout = validation.layout;
  const warnings = status.warnings ?? [];
  const stage = status.stage ?? "done";

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-6 py-3">
        <span className="rounded-full bg-slate-900 px-2.5 py-0.5 text-xs font-medium text-white">
          {layout.route}
        </span>
        <span className="text-xs text-slate-400">{STAGE_LABELS[stage] ?? stage}</span>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          {layout.items.map((item, index) => (
            <span
              key={`${item.type}-${index}`}
              className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                COMPONENT_PILLS[item.type]
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-amber-200 bg-amber-50 text-amber-700"
              }`}
            >
              {COMPONENT_PILLS[item.type] ?? item.type}
            </span>
          ))}
          {decisions.length > 0 ? (
            <span className="ml-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-0.5 text-xs text-slate-500">
              {decisions.length} decisions
            </span>
          ) : null}
        </span>
      </div>

      {warnings.length > 0 ? (
        <div className="border-b border-amber-200 bg-amber-50 px-6 py-2">
          {warnings.map((warning) => (
            <p key={warning} className="text-sm text-amber-800">
              ⚠ {warning}
            </p>
          ))}
        </div>
      ) : null}

      <div className="flex-1 overflow-auto bg-slate-100/60 p-6">
        <div className="mx-auto flex max-w-4xl flex-col gap-4">
          {status.fallback ? (
            <p className="text-xs text-slate-500">
              Sub-threshold composition — showing the catalog fallback view.
            </p>
          ) : null}
          {layout.items.map((item, index) => {
            const Component = componentFor(item.type);
            return <Component key={`${item.type}-${index}`} props={item.props} onAction={onAction} />;
          })}
        </div>
      </div>
    </div>
  );
}

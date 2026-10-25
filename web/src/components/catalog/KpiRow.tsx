/** kpi_row: 3–4 KPI tiles (PRD §7). Props.kpis: [{label, value, delta}]. */
import { Card, Skeleton, formatDelta, formatTileValue, type CatalogComponentProps } from "./shared";

interface Kpi {
  label: string;
  value: number | string;
  delta?: number | null;
}

export default function KpiRow({ props }: CatalogComponentProps) {
  const kpis = (props.kpis as Kpi[] | undefined) ?? [];
  if (kpis.length === 0) {
    return (
      <Card title="Key metrics">
        <Skeleton label="Loading KPIs…" />
      </Card>
    );
  }
  return (
    <Card>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {kpis.map((kpi) => {
          const delta = formatDelta(kpi.delta);
          return (
            <div key={kpi.label} className="rounded-lg bg-slate-50 p-3">
              <p className="text-xs font-medium text-slate-500">{kpi.label}</p>
              <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">
                {formatTileValue(kpi.label, kpi.value)}
              </p>
              <p
                className={`mt-0.5 text-xs font-medium tabular-nums ${
                  delta?.tone ?? "text-slate-400"
                }`}
              >
                {delta ? `${delta.text} vs Q2` : "vs Q2 —"}
              </p>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

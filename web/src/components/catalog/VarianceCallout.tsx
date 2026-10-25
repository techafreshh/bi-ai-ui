/** variance_callout: churn spike banner with the Investigate CTA (PRD §5.2). */
import { Card, formatMoney, type CatalogComponentProps } from "./shared";

interface CalloutProps {
  metric: string;
  delta: number;
  cause: string;
  baseline_monthly?: number;
  spike_monthly?: number;
  cause_detail?: string;
  cta?: string;
}

export default function VarianceCallout({ props, onAction }: CatalogComponentProps) {
  const p = props as unknown as CalloutProps;
  const deltaPct = typeof p.delta === "number" ? `${(p.delta * 100).toFixed(0)}%` : "—";
  return (
    <Card className="border-rose-200 bg-rose-50">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-rose-700">{p.metric}</p>
          <p className="mt-1 text-3xl font-bold tabular-nums text-rose-700">▲ {deltaPct}</p>
          <p className="mt-1 text-sm text-slate-700">
            {p.cause}
            {typeof p.baseline_monthly === "number" && typeof p.spike_monthly === "number" ? (
              <span className="text-slate-500">
                {" "}
                — {formatMoney(p.baseline_monthly)}/mo baseline → {formatMoney(p.spike_monthly)}/mo
              </span>
            ) : null}
          </p>
          {p.cause_detail ? (
            <p className="mt-1 text-xs text-slate-500">{p.cause_detail}</p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => onAction("Investigate the churn cohort")}
          className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-rose-700"
        >
          Investigate
        </button>
      </div>
    </Card>
  );
}

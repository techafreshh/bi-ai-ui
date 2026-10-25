/** data_quality_card: freshness / duplicates / nulls checks + issue CTA. */
import { Card, Skeleton, type CatalogComponentProps } from "./shared";

interface Check {
  name: string;
  status: "ok" | "fail" | string;
  score: number;
}

const STATUS_STYLES: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-800 border-emerald-200",
  fail: "bg-rose-100 text-rose-800 border-rose-200",
};

const formatScore = (check: Check): string => {
  if (check.status === "ok") return "pass";
  // Failing check scores are ratios (share of rows / breach level) → percent.
  return `${(check.score * 100).toFixed(1)}%`;
};

export default function DataQualityCard({ props, onAction }: CatalogComponentProps) {
  const checks = (props.checks as Check[] | undefined) ?? [];
  if (checks.length === 0) {
    return (
      <Card title="Data quality">
        <Skeleton label="Running checks…" />
      </Card>
    );
  }
  const failing = checks.filter((c) => c.status === "fail");
  return (
    <Card
      title={
        failing.length > 0
          ? `Data quality — ${failing.length} check${failing.length > 1 ? "s" : ""} failing`
          : "Data quality"
      }
    >
      <ul className="space-y-2">
        {checks.map((check) => {
          const style = STATUS_STYLES[check.status] ?? "bg-slate-100 text-slate-700 border-slate-200";
          return (
            <li
              key={check.name}
              className="flex items-center justify-between rounded-lg border border-slate-100 bg-slate-50 px-3 py-2"
            >
              <span className="text-sm capitalize text-slate-700">{check.name}</span>
              <span className="flex items-center gap-2">
                <span className="text-xs tabular-nums text-slate-500">{formatScore(check)}</span>
                <span
                  className={`rounded-full border px-2 py-0.5 text-xs font-semibold uppercase ${style}`}
                >
                  {check.status}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      {failing.length > 0 ? (
        <button
          type="button"
          onClick={() => onAction("Report an issue with the data")}
          className="mt-3 rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-sm font-medium text-rose-700 transition hover:bg-rose-50"
        >
          Report an issue
        </button>
      ) : null}
    </Card>
  );
}

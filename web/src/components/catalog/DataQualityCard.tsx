// Issue #12: data_quality_card — the freshness/duplicates/nulls checks
// (agent/quality.py's run_checks rows) as a beUI Alert. Variant ladder: any
// fail → destructive, else any warn → warning, else success — which also
// picks the ARIA role (alert vs status) beUI assigns per variant.

import { Alert, AlertContent, AlertDescription, AlertIcon, AlertTitle } from "../ui/alert";
import { formatScore, humanizeKey } from "../../lib/format";
import { cn } from "../../lib/utils";

// Field names verbatim from agent/quality.py's run_checks output.
export interface QualityCheck {
  name: string;
  status: "ok" | "warn" | "fail";
  score: number;
}

export interface DataQualityCardProps {
  checks: QualityCheck[];
}

const TITLE: Record<"destructive" | "warning" | "success", string> = {
  destructive: "Data quality issues detected",
  warning: "Data quality needs attention",
  success: "Data is clean",
};

const STATUS_PILL: Record<QualityCheck["status"], string> = {
  ok: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  warn: "bg-amber-500/10 text-amber-800 dark:text-amber-300",
  fail: "bg-destructive/10 text-destructive",
};

export function DataQualityCard({ checks }: DataQualityCardProps) {
  const variant = checks.some((check) => check.status === "fail")
    ? "destructive"
    : checks.some((check) => check.status === "warn")
      ? "warning"
      : "success";
  return (
    <div data-testid="catalog-data_quality_card">
      <Alert variant={variant} className="rounded-2xl p-6">
        <AlertIcon />
        <AlertContent>
          <AlertTitle>{TITLE[variant]}</AlertTitle>
          <AlertDescription>
            <ul className="space-y-1.5">
              {checks.map((check) => (
                <li
                  key={check.name}
                  data-testid="quality-check"
                  className="flex items-center justify-between gap-3"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      className={cn(
                        "rounded px-1.5 py-0.5 text-[11px] font-medium uppercase",
                        STATUS_PILL[check.status],
                      )}
                    >
                      {check.status}
                    </span>
                    <span className="truncate text-foreground">
                      {humanizeKey(check.name)}
                    </span>
                  </span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {formatScore(check.score)} confidence
                  </span>
                </li>
              ))}
            </ul>
          </AlertDescription>
        </AlertContent>
      </Alert>
    </div>
  );
}

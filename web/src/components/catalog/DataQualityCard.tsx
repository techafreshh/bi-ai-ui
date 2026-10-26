// Issue #12: data_quality_card — the freshness/duplicates/nulls checks
// (agent/quality.py's run_checks rows) as a beUI Alert. Variant ladder: any
// fail → destructive, else any warn → warning, else success — which also
// picks the ARIA role (alert vs status) beUI assigns per variant.
// Issue #17: the agent-stamped data_problem probability gates a warning
// banner (p > 0.6, mirroring the PRD's ≈0.94 data-problem reading of the
// clean-data inverted heuristic) carrying the narrated anomaly evidence and
// a Report-an-issue affordance — console log + toast only, no backend.

import { useEffect, useState } from "react";
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
  /** data_problem noul probability (agent/graph.py's stamp); the banner
   *  gate reads it — absent/invalid keeps the gate shut. */
  data_problem?: number | null;
  /** Agent-narrated anomaly evidence (window + segment) shown in the banner. */
  data_problem_detail?: string | null;
}

// The banner gate (issue acceptance: visible when p > 0.6) — a component
// constant because the DOM probe can only read rendered output.
const BANNER_THRESHOLD = 0.6;

// Toast lifetime for the Report-an-issue affordance (self-dismissing).
const TOAST_DISMISS_MS = 4000;

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

export function DataQualityCard({
  checks,
  data_problem,
  data_problem_detail,
}: DataQualityCardProps) {
  const variant = checks.some((check) => check.status === "fail")
    ? "destructive"
    : checks.some((check) => check.status === "warn")
      ? "warning"
      : "success";
  // The gate: only a real number above the threshold opens the banner.
  const showBanner =
    typeof data_problem === "number" &&
    Number.isFinite(data_problem) &&
    data_problem > BANNER_THRESHOLD;
  const [toastOpen, setToastOpen] = useState(false);
  useEffect(() => {
    if (!toastOpen) return;
    const timer = window.setTimeout(() => setToastOpen(false), TOAST_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [toastOpen]);

  const reportIssue = () => {
    console.log(
      `[a2ui] report-an-issue: ${data_problem_detail ?? "data problem suspected"}`,
    );
    setToastOpen(true);
  };

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
      {showBanner ? (
        <div
          data-testid="dq-warning"
          className="mt-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4"
        >
          <p className="text-sm font-medium text-destructive">
            Data problem suspected —{" "}
            {data_problem_detail ||
              "usage rows in the recent window look corrupted"}
          </p>
          {typeof data_problem === "number" && Number.isFinite(data_problem) ? (
            <p className="mt-1 text-xs text-muted-foreground tabular-nums">
              {Math.round(data_problem * 100)}% — this movement is better
              explained by a data-quality problem than by a real business change
            </p>
          ) : null}
          <div className="mt-2">
            <button
              type="button"
              data-testid="dq-report"
              onClick={reportIssue}
              className="inline-flex items-center rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
            >
              Report an issue
            </button>
          </div>
        </div>
      ) : null}
      {toastOpen ? (
        <div
          data-testid="dq-report-toast"
          role="status"
          className="fixed bottom-6 right-6 z-50 rounded-xl border border-border bg-popover px-4 py-3 text-sm text-popover-foreground shadow-lg"
        >
          Issue reported — details logged to the browser console. No backend
          call was made.
        </div>
      ) : null}
    </div>
  );
}

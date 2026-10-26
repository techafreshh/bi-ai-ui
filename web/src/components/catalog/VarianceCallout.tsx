// Issue #12: variance_callout — a beUI motion Alert in its warning skin,
// carrying the metric/delta/baseline/cause story and the Investigate CTA
// (framing the follow-up chat message is issue #37; here it logs + fires the
// optional onAction). Field names verbatim from agent/catalog.py's
// VarianceCalloutProps.

import { Alert, AlertAction, AlertContent, AlertDescription, AlertIcon, AlertTitle } from "../ui/alert";
import type { CatalogSize } from "./catalogSize";
import { useCatalogSize } from "./catalogSize";
import { formatDelta } from "../../lib/format";
import { cn } from "../../lib/utils";
export interface VarianceCalloutProps {
  metric: string;
  delta: number;
  baseline?: string | null;
  cause?: string | null;
  cta?: string | null;
  /** Fired when the CTA is pressed; the chat loop (issue #37) will pass it. */
  onAction?: (cta: string) => void;
}

const PADDING_CLASS: Record<CatalogSize, string> = {
  full: "p-6",
  mini: "p-4",
};

export function VarianceCallout({
  metric,
  delta,
  baseline,
  cause,
  cta,
  onAction,
}: VarianceCalloutProps) {
  const size = useCatalogSize();
  return (
    <div data-testid="catalog-variance_callout">
      {/* Always non-default skin: a variance callout is by definition a
          deviation worth noticing, regardless of the delta's sign. */}
      <Alert variant="warning" className={cn("rounded-2xl", PADDING_CLASS[size])}>
        <AlertIcon />
        <AlertContent>
          <AlertTitle data-testid="variance-metric">
            {metric === "" ? "Metric variance" : metric} moved{" "}
            <span data-testid="variance-delta" className="tabular-nums">
              {formatDelta(delta)}
            </span>
            <span className="font-normal text-muted-foreground">
              {" "}
              vs {baseline === null || baseline === undefined || baseline === "" ? "the prior period" : baseline}
            </span>
          </AlertTitle>
          {cause ? <AlertDescription>{cause}</AlertDescription> : null}
          {cta ? (
            <AlertAction>
              <button
                type="button"
                data-testid="variance-cta"
                onClick={() => {
                  console.info(`[a2ui] investigate: ${metric} — ${cta}`);
                  onAction?.(cta);
                }}
                className="inline-flex items-center rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
              >
                Investigate {cta}
              </button>
            </AlertAction>
          ) : null}
        </AlertContent>
      </Alert>
    </div>
  );
}

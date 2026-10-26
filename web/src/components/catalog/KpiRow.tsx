// Issue #12: kpi_row — 3–4 KPI tiles on beUI motion Cards (PRD §7). Field
// names verbatim from agent/catalog.py's KpiEntry: {label, value, delta}.

import { Minus, TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardContent, CardDescription } from "../ui/card";
import { AnimatedNumber } from "../ui/number";
import type { CatalogSize } from "./catalogSize";
import { useCatalogSize } from "./catalogSize";
import { formatCurrency, formatDelta } from "../../lib/format";
import { cn } from "../../lib/utils";

// Mirrors the KpiEntry shape the A2UI wire contract carries (agent/catalog.py;
// flipped in a fixtures file: JSON has no int/float distinction).
export interface KpiEntry {
  label: string;
  value: number;
  delta: number;
}

export interface KpiRowProps {
  kpis: KpiEntry[];
}

const GAP_CLASS: Record<CatalogSize, string> = {
  full: "gap-4",
  mini: "gap-2",
};

// Delta pill: gains in the beUI success green, losses in destructive red,
// zero muted — same hue language as the Alert variants.
function DeltaPill({ delta }: { delta: number }) {
  const Icon = delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium tabular-nums",
        delta > 0 && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        delta < 0 && "bg-destructive/10 text-destructive",
        delta === 0 && "bg-muted text-muted-foreground",
      )}
    >
      <Icon aria-hidden="true" className="size-3" />
      {formatDelta(delta)}
    </span>
  );
}

export function KpiRow({ kpis }: KpiRowProps) {
  const size = useCatalogSize();
  return (
    <section
      data-testid="catalog-kpi_row"
      className={cn("grid grid-cols-2 xl:grid-cols-4", GAP_CLASS[size])}
    >
      {kpis.map((kpi) => (
        <Card key={kpi.label} size="compact">
          <CardContent>
            <CardDescription className="truncate">{kpi.label}</CardDescription>
            <div className="mt-1 text-2xl font-semibold tracking-tight">
              <AnimatedNumber value={kpi.value} format={formatCurrency} />
            </div>
            <div className="mt-1.5 flex items-center gap-1.5">
              <DeltaPill delta={kpi.delta} />
              <span className="text-xs text-muted-foreground">vs prior period</span>
            </div>
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

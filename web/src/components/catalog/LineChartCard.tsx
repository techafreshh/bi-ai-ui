// Issue #12: line_chart — a Recharts LineChart in a beUI Card. The wire
// contract carries only {series, range, annotations} (agent/catalog.py's
// LineChartProps); data resolves through lib/series.ts's named-series table.

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../ui/card";
import type { CatalogSize } from "./catalogSize";
import { useCatalogSize } from "./catalogSize";
import { formatCurrency } from "../../lib/format";
import { buildSeries } from "../../lib/series";

// Field names verbatim from agent/catalog.py's LineChartProps; annotations
// default to [] because the server-side model defaults it too.
export interface LineChartCardProps {
  series: string;
  range: string;
  annotations?: string[];
}

const CHART_HEIGHT: Record<CatalogSize, number> = {
  full: 320,
  mini: 180,
};

const HUMAN_SERIES: Record<string, string> = {
  arr_daily: "ARR, daily",
};

export function LineChartCard({ series, range, annotations }: LineChartCardProps) {
  const size = useCatalogSize();
  const points = buildSeries(series, range);
  return (
    <Card data-testid="catalog-line_chart" className="h-full">
      <CardHeader>
        <CardTitle>{HUMAN_SERIES[series] ?? series}</CardTitle>
        <CardDescription>{range.replace("..", " → ")}</CardDescription>
      </CardHeader>
      <CardContent>
        {points.length === 0 ? (
          // Validated props but an unknown series/range: an in-card empty
          // state, NOT a fallback card — only schema failures degrade that far.
          <div
            data-testid="line-chart-empty"
            className="flex items-center justify-center rounded-lg border border-dashed border-border bg-muted/40 p-10 text-sm text-muted-foreground"
          >
            No data for this series
          </div>
        ) : (
          <div data-testid="line-chart" data-points={points.length}>
            <ResponsiveContainer width="100%" height={CHART_HEIGHT[size]}>
              <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                  tickLine={false}
                  axisLine={{ stroke: "var(--border)" }}
                  minTickGap={40}
                  tickFormatter={(value: string) => value.slice(5)}
                />
                <YAxis
                  width={72}
                  tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value: number) => formatCurrency(value)}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  cursor={{ stroke: "var(--border-strong)" }}
                  formatter={(value) => formatCurrency(Number(value))}
                  labelFormatter={(label) => String(label)}
                />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="var(--primary)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 3 }}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
      {annotations && annotations.length > 0 ? (
        <CardFooter className="text-xs text-muted-foreground">
          {annotations.join(" · ")}
        </CardFooter>
      ) : null}
    </Card>
  );
}

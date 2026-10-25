/** line_chart: ARR series on Recharts with event annotations (PRD §7). */
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, Skeleton, formatMoney, type CatalogComponentProps } from "./shared";

interface ChartPoint {
  day: string;
  net_new: number;
  churned: number;
}

interface Annotation {
  day: string;
  label: string;
}

const shortDay = (day: string) => day.slice(5); // MM-DD
const shortMoney = (value: number) => `$${Math.round(value / 1000)}k`;

export default function LineChartCat({ props }: CatalogComponentProps) {
  const points = (props.points as ChartPoint[] | undefined) ?? [];
  const annotations = (props.annotations as Annotation[] | undefined) ?? [];
  const range = typeof props.range === "string" ? props.range : "";
  if (points.length === 0) {
    return (
      <Card title={`Daily net-new ARR${range ? ` · ${range}` : ""}`}>
        <Skeleton label="Loading series…" />
      </Card>
    );
  }
  return (
    <Card title={`Daily net-new ARR${range ? ` · ${range}` : ""}`}>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="day" tickFormatter={shortDay} fontSize={11} tickLine={false} />
            <YAxis tickFormatter={shortMoney} fontSize={11} tickLine={false} width={56} />
            <Tooltip
              formatter={(value: number | string, name) => [
                formatMoney(Number(value)),
                name === "net_new" ? "Net-new" : "Churned",
              ]}
              labelFormatter={(day) => String(day)}
            />
            {annotations.map((a) => (
              <ReferenceLine
                key={a.day}
                x={a.day}
                stroke="#64748b"
                strokeDasharray="4 4"
                label={{ value: a.label, fontSize: 10, position: "insideTopRight" }}
              />
            ))}
            <Line
              type="monotone"
              dataKey="net_new"
              name="net_new"
              stroke="#16a34a"
              strokeWidth={2}
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="churned"
              name="churned"
              stroke="#dc2626"
              strokeWidth={1.5}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        <span className="font-medium text-emerald-700">green</span> net-new ·{" "}
        <span className="font-medium text-rose-700">red</span> churned · dashed = events
      </p>
    </Card>
  );
}

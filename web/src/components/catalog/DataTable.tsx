/** data_table: churned-account cohort, columns mirrored from agent SQL. */
import { Card, Skeleton, formatMoney, type CatalogComponentProps } from "./shared";

const moneyColumns = new Set(["mrr"]);

const formatCell = (column: string, value: unknown): string => {
  if (value === null || value === undefined) return "—";
  if (moneyColumns.has(column) && typeof value === "number") return formatMoney(value);
  if (column === "tenure_months" && typeof value === "number") return `${value} mo`;
  return String(value);
};

export default function DataTable({ props }: CatalogComponentProps) {
  const columns = (props.columns as string[] | undefined) ?? [];
  const rows = (props.rows as Record<string, unknown>[] | undefined) ?? [];
  if (columns.length === 0) {
    return (
      <Card title="Accounts">
        <Skeleton label="Loading rows…" />
      </Card>
    );
  }
  return (
    <Card title={`Accounts (${rows.length} rows)`}>
      <div className="max-h-80 overflow-auto rounded-lg border border-slate-200">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="sticky top-0 bg-slate-100">
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  className={`px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-600 ${
                    moneyColumns.has(column) ? "text-right" : "text-left"
                  }`}
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {rows.map((row, index) => (
              /* Row identity: order is meaningful (agent sorts by MRR desc) and
                 account names can repeat, so index keys are correct here. */
              <tr key={index} className="hover:bg-slate-50">
                {columns.map((column) => (
                  <td
                    key={column}
                    className={`whitespace-nowrap px-3 py-1.5 text-slate-700 ${
                      moneyColumns.has(column) ? "text-right tabular-nums" : ""
                    }`}
                  >
                    {formatCell(column, row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// Issue #12: data_table — a beUI-derived virtualized table in a Card.
// Field names verbatim from agent/catalog.py's DataTableProps: columns +
// rows (≤50 per PRD §7) + filters (the columns that get filter inputs).

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { VirtualTable } from "../ui/virtual-table";
import type { CatalogSize } from "./catalogSize";
import { useCatalogSize } from "./catalogSize";

export interface DataTableCardProps {
  columns: string[];
  rows: Record<string, unknown>[];
  filters?: string[];
}

const TABLE_HEIGHT: Record<CatalogSize, number> = {
  full: 420,
  mini: 240,
};

export function DataTableCard({ columns, rows, filters }: DataTableCardProps) {
  const size = useCatalogSize();
  return (
    <Card data-testid="catalog-data_table" className="h-full">
      <CardHeader>
        <CardTitle>Data table</CardTitle>
        <CardDescription>
          {rows.length} {rows.length === 1 ? "row" : "rows"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <VirtualTable
          columns={columns}
          rows={rows}
          filters={filters}
          height={TABLE_HEIGHT[size]}
        />
      </CardContent>
    </Card>
  );
}

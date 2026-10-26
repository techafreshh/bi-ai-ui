"use client";
// beUI-derived virtualized table (beui.dev/components/motion/table), reduced
// to what the data_table catalog contract needs: fixed-layout columns,
// tanstack virtualization with the spacer-window tbody idiom, and — the one
// thing beUI has no copy for — per-column filter inputs driven by a
// validated `filters: string[]`. Dropped: selection, sorting, resize,
// reorder, cell editing, row menus, skeletons, checkboxes (the schema caps
// data_table at 50 rows, so a 10-file editing grid would be 10x dead code).

import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { humanizeKey } from "../../lib/format";
import { cn } from "../../lib/utils";

export interface VirtualTableProps {
  columns: string[];
  rows: Record<string, unknown>[];
  /** Column keys that get a filter input; filtering is a case-insensitive substring match. */
  filters?: readonly string[];
  /** Scroll viewport height in px. */
  height?: number;
  /** Cell renderer override (data_table cells are String(value) by default). */
  renderCell?: (key: string, row: Record<string, unknown>) => ReactNode;
  className?: string;
}

const ROW_HEIGHT = 44;

function cellText(key: string, row: Record<string, unknown>): string {
  const value = row[key];
  return value === null || value === undefined ? "—" : String(value);
}

export function VirtualTable({
  columns,
  rows,
  filters = [],
  height = 420,
  renderCell,
  className,
}: VirtualTableProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const filterSet = useMemo(
    () => columns.filter((column) => filters.includes(column)),
    [columns, filters],
  );

  // Per-column filter text ("" matches everything); a row survives when EVERY
  // filtered column's text contains its query, case-insensitively.
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});

  const effectiveRows = useMemo(() => {
    const queries = Object.entries(filterValues).filter(
      ([, query]) => query !== "",
    );
    if (queries.length === 0) return rows;
    return rows.filter((row) =>
      queries.every(([column, query]) =>
        cellText(column, row).toLowerCase().includes(query.toLowerCase()),
      ),
    );
  }, [rows, filterValues]);

  // overscan floors at the full list: the schema caps data_table at 50 rows,
  // so virtualization stays wired while every row legitimately mounts to the
  // DOM (the acceptance contract counts row elements).
  //
  // observeElementRect is replaced with a no-op and the viewport itself is
  // declared via initialRect: the scroller is a FIXED-height div, so the
  // rect is a constant of this component — and jsdom's getBoundingClientRect
  // is all zeros, which would otherwise empty the virtual window entirely.
  const virtualizer = useVirtualizer({
    count: effectiveRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: Math.max(10, effectiveRows.length),
    initialRect: { width: 1024, height },
    observeElementRect: () => {},
  });

  const virtualItems = virtualizer.getVirtualItems();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom =
    virtualItems.length > 0
      ? virtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end
      : 0;

  const setFilter = (column: string, value: string) => {
    setFilterValues((current) => ({ ...current, [column]: value }));
  };

  return (
    <div
      data-testid="data-table"
      className={cn(
        "w-full overflow-hidden rounded-lg border border-border bg-card text-sm",
        className,
      )}
    >
      <div ref={scrollRef} className="overflow-auto" style={{ height }}>
        <table
          className="w-full border-collapse"
          style={{ tableLayout: "fixed" }}
        >
          <colgroup>
            {columns.map((column) => (
              <col key={column} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="border-b border-border bg-muted px-3 py-2 text-left font-medium text-muted-foreground"
                >
                  {humanizeKey(column)}
                </th>
              ))}
            </tr>
            {filterSet.length > 0 && (
              <tr>
                {columns.map((column) => (
                  <th key={column} className="border-b border-border px-2 py-1.5">
                    {filterSet.includes(column) ? (
                      <input
                        type="text"
                        aria-label={`Filter ${column}`}
                        data-testid={`filter-${column}`}
                        value={filterValues[column] ?? ""}
                        onChange={(event) =>
                          setFilter(column, event.target.value)
                        }
                        placeholder="Filter…"
                        className="w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-normal text-foreground outline-none placeholder:text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-ring"
                      />
                    ) : null}
                  </th>
                ))}
              </tr>
            )}
          </thead>
          <tbody>
            {virtualItems.length === 0 ? (
              <tr>
                <td
                  colSpan={Math.max(1, columns.length)}
                  className="p-10 text-center text-muted-foreground"
                >
                  No data
                </td>
              </tr>
            ) : (
              <>
                {paddingTop > 0 ? (
                  <tr aria-hidden style={{ height: paddingTop }}>
                    <td colSpan={columns.length} />
                  </tr>
                ) : null}
                {virtualItems.map((virtualItem) => {
                  const row = effectiveRows[virtualItem.index];
                  return (
                    <tr
                      key={virtualItem.key}
                      data-testid="data-table-row"
                      style={{ height: ROW_HEIGHT }}
                      className="border-border/60 border-b transition-colors hover:bg-muted/50"
                    >
                      {columns.map((column) => (
                        <td
                          key={column}
                          className="truncate px-3 text-card-foreground"
                        >
                          {renderCell ? renderCell(column, row) : cellText(column, row)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
                {paddingBottom > 0 ? (
                  <tr aria-hidden style={{ height: paddingBottom }}>
                    <td colSpan={columns.length} />
                  </tr>
                ) : null}
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

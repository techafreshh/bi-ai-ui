// Issue #70: the honest empty state. A fresh clone boots with no dataset,
// so the canvas defaults to this static text instead of a demo fixture —
// the exact contract string the acceptance probe greps for on `/`.
export function EmptyState() {
  return (
    <section
      data-testid="empty-state"
      className="mx-auto flex w-full max-w-6xl flex-col items-center gap-2 py-16 text-center"
    >
      <p className="text-lg font-medium text-foreground">
        No dataset loaded — upload CSV or load sample
      </p>
      <p className="text-sm text-muted-foreground">
        Boot stays green with no dataset; add data to see the dashboard.
      </p>
    </section>
  );
}

/**
 * Catalog component props contract: the layout item's props plus the shared
 * action hook for components with CTAs (VarianceCallout → Investigate,
 * DataQualityCard → Report an issue). CTAs append a framed user message to
 * the shared agent thread, which the route classifier turns into drill-down
 * runs (PRD §10 "Investigate CTA = framed follow-up message").
 */
import type { ReactNode } from "react";

export interface CatalogComponentProps {
  props: Record<string, unknown>;
  onAction: (text: string) => void;
}

export function Card({
  children,
  title,
  className = "",
}: {
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${className}`}
    >
      {title ? (
        <h3 className="mb-3 text-sm font-semibold text-slate-700">{title}</h3>
      ) : null}
      {children}
    </section>
  );
}

export function Skeleton({ label }: { label: string }) {
  return (
    <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50">
      <p className="text-sm text-slate-400">{label}</p>
    </div>
  );
}

/* ------------------------------- helpers ------------------------------- */

export function formatMoney(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatTileValue(label: string, value: unknown): string {
  if (typeof value !== "number") return String(value ?? "—");
  if (/ARR|MRR/i.test(label)) return formatMoney(value);
  if (/retention/i.test(label)) return value.toFixed(3);
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatDelta(delta: unknown): { text: string; tone: string } | null {
  if (typeof delta !== "number" || Number.isNaN(delta)) return null;
  const pct = `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(1)}%`;
  return {
    text: pct,
    tone: delta > 0 ? "text-emerald-600" : delta < 0 ? "text-rose-600" : "text-slate-500",
  };
}

/** insight_text: narrative slot (title + body from agent slotCopy/props). */
import { Card, type CatalogComponentProps } from "./shared";

interface InsightProps {
  title: string;
  body: string;
}

export default function InsightText({ props }: CatalogComponentProps) {
  const p = props as unknown as InsightProps;
  return (
    <Card title={p.title || "Narrative"}>
      <p className="whitespace-pre-line text-sm leading-relaxed text-slate-700">{p.body || "…"}</p>
    </Card>
  );
}

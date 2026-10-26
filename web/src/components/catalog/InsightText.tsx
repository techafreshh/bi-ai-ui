// Issue #12: insight_text — the agent's narrative as a beUI text block on a
// muted Card. Field names verbatim from agent/catalog.py's InsightTextProps.

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";

export interface InsightTextProps {
  title: string;
  body: string;
}

export function InsightText({ title, body }: InsightTextProps) {
  return (
    <Card variant="muted" data-testid="catalog-insight_text" className="h-full">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <CardDescription className="text-sm leading-relaxed">
          {body}
        </CardDescription>
      </CardContent>
    </Card>
  );
}

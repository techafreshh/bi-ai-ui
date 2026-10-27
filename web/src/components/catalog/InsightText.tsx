// Issue #12: insight_text — the agent's narrative as a beUI text block on a
// muted Card. Field names verbatim from agent/catalog.py's InsightTextProps.

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Skeleton } from "../ui/skeleton";
import { useSlotCopy } from "./slotCopy";

export interface InsightTextProps {
  title: string;
  body: string;
}

export function InsightText({ title, body }: InsightTextProps) {
  // Issue #13: the body is the insight_text.body slot — streamed copy wins,
  // a pending slot with no copy yet renders the skeleton, and the props
  // body stays the default-context fallback (fixture mode).
  const { slotCopy, slotsPending } = useSlotCopy();
  const copy = slotCopy["insight_text.body"];
  return (
    <Card variant="muted" data-testid="catalog-insight_text" className="h-full">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {slotsPending && !copy ? (
          <div data-testid="slot-skeleton" className="flex flex-col gap-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
          </div>
        ) : (
          <CardDescription className="text-sm leading-relaxed">
            {copy ?? body}
          </CardDescription>
        )}
      </CardContent>
    </Card>
  );
}

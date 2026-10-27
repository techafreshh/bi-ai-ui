// Issue #13: the pulse primitive for skeleton-then-fill — the canvas grid
// and the slot placeholders share one beUI-muted pulse so streaming reads
// as one surface.

import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      {...props}
      className={cn("animate-pulse rounded-md bg-muted", className)}
    />
  );
}

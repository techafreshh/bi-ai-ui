// Issue #12: the fullscreen dashboard canvas. Fixture-driven (DEMO_LAYOUT)
// until the chat issues land — the layout flows through the same
// A2UIRenderer pipeline agent state will, so the acceptance contract
// (exactly 6 registry-resolved components, no fallback cards, no placeholders)
// is the real render path, not a special case.
//
// Issue #13: once the agent streams, the canvas follows the useA2UI phase
// machine instead of the fixture: pending shows the skeleton grid within
// 400ms of the sent message, layout/filled render the raw streamed envelope
// through the UNCHANGED A2UIRenderer under the SlotCopyProvider (an invalid
// envelope degrades to the renderer's fallback card — never a stuck
// skeleton). Idle keeps the exact #12 fixture behavior.

import { DEMO_LAYOUT } from "../lib/demoLayout";
import { AGENT_ID } from "../lib/agent";
import { useA2UI, slotCopyOf } from "../hooks/useA2UI";
import { A2UIRenderer } from "./catalog/A2UIRenderer";
import { CatalogSizeProvider } from "./catalog/catalogSize";
import { SlotCopyProvider } from "./catalog/slotCopy";
import { Skeleton } from "./ui/skeleton";

function CanvasSkeleton() {
  return (
    <div
      data-testid="canvas-skeleton"
      className="grid grid-cols-2 gap-4 xl:grid-cols-4"
      aria-label="Loading dashboard"
    >
      <Skeleton className="col-span-2 h-28 xl:col-span-4" />
      <Skeleton className="col-span-2 h-80" />
      <Skeleton className="h-44" />
      <Skeleton className="h-44" />
      <Skeleton className="col-span-2 h-56 xl:col-span-4" />
    </div>
  );
}

export function DashboardCanvas({
  // Test/alternate-layout escape hatch; defaults to the demo fixture.
  layout = DEMO_LAYOUT,
}: {
  layout?: unknown;
}) {
  const { a2ui, phase } = useA2UI(AGENT_ID);

  if (phase === "pending") {
    return (
      <CatalogSizeProvider value="full">
        <section
          data-testid="dashboard-canvas"
          className="mx-auto flex w-full max-w-6xl flex-col gap-4"
        >
          <CanvasSkeleton />
        </section>
      </CatalogSizeProvider>
    );
  }

  if ((phase === "layout" || phase === "filled") && a2ui !== null) {
    return (
      <CatalogSizeProvider value="full">
        <SlotCopyProvider
          value={{ slotCopy: slotCopyOf(a2ui), slotsPending: phase === "layout" }}
        >
          <section
            data-testid="dashboard-canvas"
            className="mx-auto flex w-full max-w-6xl flex-col gap-4"
          >
            <A2UIRenderer layout={a2ui} />
          </section>
        </SlotCopyProvider>
      </CatalogSizeProvider>
    );
  }

  return (
    <CatalogSizeProvider value="full">
      <section
        data-testid="dashboard-canvas"
        className="mx-auto flex w-full max-w-6xl flex-col gap-4"
      >
        <A2UIRenderer layout={layout} />
      </section>
    </CatalogSizeProvider>
  );
}

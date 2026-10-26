// Issue #12: the fullscreen dashboard canvas. Fixture-driven (DEMO_LAYOUT)
// until the chat issues land — the layout flows through the same
// A2UIRenderer pipeline agent state will, so the acceptance contract
// (exactly 6 registry-resolved components, no fallback cards) is the real
// render path, not a special case.

import { DEMO_LAYOUT } from "../lib/demoLayout";
import { A2UIRenderer } from "./catalog/A2UIRenderer";
import { CatalogSizeProvider } from "./catalog/catalogSize";

export function DashboardCanvas({
  // Test/alternate-layout escape hatch; defaults to the demo fixture.
  layout = DEMO_LAYOUT,
}: {
  layout?: unknown;
}) {
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

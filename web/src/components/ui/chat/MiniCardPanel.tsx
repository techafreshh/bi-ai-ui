// Issue #13: the inline mini-card for the last chat turn — a compact,
// bounded rendering of the streamed layout inside the assistant bubble.
// Pure: the layout funnels through the SAME A2UIRenderer the canvas uses
// (no forked render path), wrapped in the mini catalog size so every
// component takes its compact variant. LatestMiniCard gates visibility on
// the last message with a filled layout — historical turns keep their text,
// and a layout-phase (copy still pending) bubble shows no stale tiles.

import { A2UIRenderer } from "../../catalog/A2UIRenderer";
import { CatalogSizeProvider } from "../../catalog/catalogSize";
import { SlotCopyProvider } from "../../catalog/slotCopy";
import { slotCopyOf, type A2UIPhase } from "../../../hooks/useA2UI";

export function MiniCardPanel({ layout }: { layout: unknown }) {
  // Self-provisioned copy: the gate only opens on a filled layout, so the
  // envelope's own slotCopy is the filled narrative — no pending slots in
  // the mini panel, and the titles match the canvas verbatim.
  return (
    <div
      data-testid="chat-minicard"
      className="max-h-[200px] overflow-y-auto rounded-xl border border-border bg-card p-3"
    >
      <CatalogSizeProvider value="mini">
        <SlotCopyProvider
          value={{ slotCopy: slotCopyOf(layout), slotsPending: false }}
        >
          <A2UIRenderer layout={layout} />
        </SlotCopyProvider>
      </CatalogSizeProvider>
    </div>
  );
}

export function LatestMiniCard({
  isLatest,
  a2ui,
  phase,
}: {
  isLatest?: boolean;
  a2ui: unknown | null;
  phase: A2UIPhase;
}) {
  if (!isLatest || phase !== "filled" || a2ui === null) return null;
  return <MiniCardPanel layout={a2ui} />;
}

import type { ReactElement } from "react";
import {
  parseA2UILayout,
  validateComponentProps,
} from "../../lib/a2ui";
import { getComponent } from "./registry";

// PRD §10: the frontend re-validates every layout the agent streams; an
// unknown type or an invalid payload must degrade to a fallback card —
// never an error, never a crash (issue #11 acceptance).

// The amber variant of the App.tsx card idiom (rounded-xl / border /
// shadow-sm): the one visual contract a degraded render must keep.
export function FallbackCard({ title, detail }: { title: string; detail?: string }) {
  return (
    <div
      data-testid="a2ui-fallback"
      className="rounded-xl border border-amber-300 bg-amber-50 p-6 shadow-sm"
    >
      <h3 className="text-base font-medium text-amber-900">{title}</h3>
      {detail ? <p className="mt-1 text-sm text-amber-700">{detail}</p> : null}
    </div>
  );
}

// Single choke point for the fallback decision table; each fallback card
// gets exactly one decision-log-style console.error (issue #11: unknown type
// or invalid payload → fallback card + decision-log warning).
function renderItem(item: unknown, index: number): ReactElement {
  if (typeof item !== "object" || item === null) {
    console.error(
      `[a2ui] item is not an object (${typeof item}) — rendered fallback card`,
    );
    return (
      <FallbackCard
        key={index}
        title="Invalid component"
        detail={`expected an item object, got ${typeof item}`}
      />
    );
  }
  const { type, props } = item as { type?: unknown; props?: unknown };
  if (typeof type !== "string" || type.length === 0) {
    console.error(`[a2ui] item has no "type" string — rendered fallback card`);
    return (
      <FallbackCard
        key={index}
        title="Invalid component"
        detail='item is missing a "type" string'
      />
    );
  }
  const Component = getComponent(type);
  if (!Component) {
    console.error(
      `[a2ui] unknown component type "${type}" — rendered fallback card`,
    );
    return <FallbackCard key={index} title={`Unknown component: ${type}`} />;
  }
  const checked = validateComponentProps(type, props);
  if (!checked.ok) {
    console.error(
      `[a2ui] props failed validation for component "${type}" — rendered fallback card (${checked.error})`,
    );
    return (
      <FallbackCard
        key={index}
        title={`Invalid component: ${type}`}
        detail={checked.error}
      />
    );
  }
  // Props were validated by validateComponentProps against the type's schema;
  // placeholders ignore them, real components consume known keys.
  return <Component key={index} {...(checked.data as Record<string, unknown>)} />;
}

export type A2UIRendererProps = {
  // Raw item list (extracted from agent state or a test) — rendered through
  // the per-item pipeline.
  items?: readonly unknown[];
  // Raw agent payload (PRD §10) — validated as a whole first; a failure
  // renders the single "Invalid layout" card.
  layout?: unknown;
};

export function A2UIRenderer({ items, layout }: A2UIRendererProps): ReactElement | null {
  // The layout path wins when both are given: the envelope is the stronger
  // contract and decides what the item list even is.
  if (layout !== undefined) {
    const parsed = parseA2UILayout(layout);
    if (!parsed.ok) {
      console.error(
        `[a2ui] layout failed Zod validation — rendered fallback card (${parsed.error})`,
      );
      return <FallbackCard title="Invalid layout" detail={parsed.error} />;
    }
    return <>{parsed.data.items.map((item, index) => renderItem(item, index))}</>;
  }
  if (items === undefined) return null;
  if (!Array.isArray(items)) {
    console.error(`[a2ui] items prop is not an array — rendered fallback card`);
    return <FallbackCard title="Invalid items" detail="items prop must be an array" />;
  }
  // Zero valid items render nothing — caller decides what an empty canvas
  // means (the default-insight view is a later issue).
  if (items.length === 0) return null;
  return <>{items.map((item, index) => renderItem(item, index))}</>;
}

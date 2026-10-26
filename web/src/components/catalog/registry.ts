import { createElement, type FC } from "react";
import { COMPONENT_TYPES, type A2UIComponentType } from "../../lib/a2ui";

// Issue #11 / PRD §7: the component registry maps the six fixed catalog type
// strings to React components. All six are placeholders for now — the real
// beUI/Recharts implementations land in a later phase — but the map, the
// lookup and the unknown-type miss (→ fallback card) are the contract here.

// Every catalog component receives its validated props as a flat string-keyed
// record; real components will consume known keys from it.
export type CatalogComponent = FC<Record<string, unknown>>;

// Temporary stand-in rendering the type name and a note that the real
// component is pending — the visible proof that a known type was routed.
// Built with createElement instead of JSX because issue #11 pins this file
// as registry.ts and esbuild only parses JSX in .tsx files.
export function makePlaceholder(type: string): CatalogComponent {
  function Placeholder() {
    return createElement(
      "div",
      {
        "data-testid": `catalog-placeholder-${type}`,
        className: "rounded-xl border border-slate-200 bg-white p-6 shadow-sm",
      },
      createElement("h3", { className: "text-base font-medium" }, type),
      createElement(
        "p",
        { className: "mt-1 text-sm text-slate-500" },
        "Catalog placeholder — real component lands in a later phase.",
      ),
    );
  }
  return Placeholder;
}

// Module-level, built once: keys are exactly COMPONENT_TYPES, so adding a
// catalog type in one place wires it through schema, registry and tests.
export const componentRegistry = Object.fromEntries(
  COMPONENT_TYPES.map((type) => [type, makePlaceholder(type)]),
) as Record<A2UIComponentType, CatalogComponent>;

export function getComponent(type: string): CatalogComponent | undefined {
  // Own-property-only lookup: as a plain-object map, registry["toString"]
  // and friends resolve to inherited Object.prototype members and would route
  // an unknown type into the known-component path (crashing prop validation)
  // instead of the fallback card. Object.hasOwn misses closed for them.
  if (!Object.hasOwn(componentRegistry, type)) return undefined;
  return componentRegistry[type as A2UIComponentType];
}

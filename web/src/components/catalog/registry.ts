import { createElement, type FC } from "react";
import { COMPONENT_TYPES, type A2UIComponentType } from "../../lib/a2ui";
import { DataQualityCard } from "./DataQualityCard";
import { DataTableCard } from "./DataTableCard";
import { InsightText } from "./InsightText";
import { KpiRow } from "./KpiRow";
import { LineChartCard } from "./LineChartCard";
import { VarianceCallout } from "./VarianceCallout";

// Issue #11 / PRD §7: the component registry maps the six fixed catalog type
// strings to React components. Since issue #12 every entry is the real beUI/
// Recharts implementation; the map shape, the lookup and the unknown-type
// miss (→ fallback card) are unchanged contract.

// Every catalog component receives its validated props as a flat string-keyed
// record. The adapter is the single point where "validated unknown" becomes
// the component's typed props — validateComponentProps has already enforced
// each field against the schema, so the cast cannot observe an off-contract
// key riding in from the wire.
export type CatalogComponent = FC<Record<string, unknown>>;

function adapt<P>(Component: FC<P>): CatalogComponent {
  function Adapted(props: Record<string, unknown>) {
    // Re-typing to the wide record keeps the call inside createElement's
    // overload; the cast below is the validated-unknown -> typed narrow.
    const Wide = Component as unknown as FC<Record<string, unknown>>;
    return createElement(Wide, props);
  }
  return Adapted;
}

// Module-level, built once: keyed by COMPONENT_TYPES so adding a catalog
// type in one place wires it through schema, registry and tests, and the
// exported map's key order stays the fixed catalog order.
const catalogComponents: Record<A2UIComponentType, CatalogComponent> = {
  kpi_row: adapt(KpiRow),
  line_chart: adapt(LineChartCard),
  variance_callout: adapt(VarianceCallout),
  data_table: adapt(DataTableCard),
  data_quality_card: adapt(DataQualityCard),
  insight_text: adapt(InsightText),
};

export const componentRegistry = Object.fromEntries(
  COMPONENT_TYPES.map((type) => [type, catalogComponents[type]]),
) as Record<A2UIComponentType, CatalogComponent>;

export function getComponent(type: string): CatalogComponent | undefined {
  // Own-property-only lookup: as a plain-object map, registry["toString"]
  // and friends resolve to inherited Object.prototype members and would route
  // an unknown type into the known-component path (crashing prop validation)
  // instead of the fallback card. Object.hasOwn misses closed for them.
  if (!Object.hasOwn(componentRegistry, type)) return undefined;
  return componentRegistry[type as A2UIComponentType];
}

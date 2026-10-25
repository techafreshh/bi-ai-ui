/**
 * Component registry: item type → React component (PRD §6 "component
 * registry"). Unknown types map to FallbackCard — never an error.
 */
import type { ComponentType } from "react";
import type { A2UIItem } from "../../lib/a2ui";
import type { CatalogComponentProps } from "./shared";
import DataQualityCard from "./DataQualityCard";
import DataTable from "./DataTable";
import FallbackCard from "./FallbackCard";
import InsightText from "./InsightText";
import KpiRow from "./KpiRow";
import LineChartCat from "./LineChart";
import VarianceCallout from "./VarianceCallout";

export const CATALOG: Record<string, ComponentType<CatalogComponentProps>> = {
  kpi_row: KpiRow,
  line_chart: LineChartCat,
  variance_callout: VarianceCallout,
  data_table: DataTable,
  data_quality_card: DataQualityCard,
  insight_text: InsightText,
};

export function componentFor(type: string): ComponentType<CatalogComponentProps> {
  return CATALOG[type] ?? FallbackCard;
}

export function isKnownType(item: A2UIItem): item is A2UIItem & { type: keyof typeof CATALOG } {
  return item.type in CATALOG;
}

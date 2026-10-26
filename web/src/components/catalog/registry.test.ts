// Contract tests for the component registry (issue #11, real components
// since issue #12).
//
// Pinned contract: componentRegistry has exactly one entry per COMPONENT_TYPES
// member, keyed in the same fixed order, and getComponent misses closed for
// off-catalog types — the renderer's unknown-type check relies on that miss.
// Every entry is also smoke-rendered with minimal valid props: the registry
// route must produce its real catalog component (data-testid="catalog-<type>"),
// never a fallback or placeholder.

import { cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COMPONENT_TYPES } from "../../lib/a2ui";
import { componentRegistry, getComponent } from "./registry";

// Minimal valid props per type — enough to pass validateComponentProps and
// render the real component's settled first frame.
const SMOKE_PROPS: Record<string, Record<string, unknown>> = {
  kpi_row: { kpis: [{ label: "ARR", value: 1200000, delta: 0.18 }] },
  line_chart: { series: "arr_daily", range: "2026-07-01..2026-07-03" },
  variance_callout: { metric: "MRR", delta: 0.42 },
  data_table: { columns: ["account"], rows: [{ account: "Acme" }] },
  data_quality_card: {
    checks: [{ name: "freshness", status: "ok", score: 1 }],
  },
  insight_text: { title: "T", body: "B" },
};

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("componentRegistry", () => {
  it("has exactly the catalog types as keys, in the fixed order", () => {
    expect(Object.keys(componentRegistry)).toEqual([...COMPONENT_TYPES]);
  });

  it("maps every type to a component function, the same instance per type", () => {
    for (const type of COMPONENT_TYPES) {
      const entry = getComponent(type);
      expect(typeof entry).toBe("function");
      expect(getComponent(type)).toBe(entry);
    }
  });

  it("misses closed for off-catalog types", () => {
    expect(getComponent("bogus_chart")).toBeUndefined();
    expect(getComponent("")).toBeUndefined();
  });

  it("misses closed for type strings naming Object.prototype members (prototype-chain poisoning)", () => {
    // Regression: a plain-object registry inherits Object.prototype, so
    // getComponent("toString") used to resolve truthy and take the
    // known-component path — crashing prop validation instead of rendering
    // the "Unknown component" fallback card (issue #11: never an error).
    for (const type of [
      "toString",
      "valueOf",
      "hasOwnProperty",
      "constructor",
      "__proto__",
    ]) {
      expect(getComponent(type)).toBeUndefined();
    }
  });

  it("renders each entry as its real catalog component, silently", () => {
    for (const type of COMPONENT_TYPES) {
      const Component = getComponent(type);
      expect(Component).toBeDefined();
      const { container } = render(
        createElement(Component!, SMOKE_PROPS[type]),
      );
      const root = container.querySelector(`[data-testid="catalog-${type}"]`);
      expect(root).not.toBeNull();
      expect(container.querySelector('[data-testid="a2ui-fallback"]')).toBeNull();
      expect(console.error).not.toHaveBeenCalled();
      cleanup();
    }
  });
});

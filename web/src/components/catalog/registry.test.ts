// Contract tests for the component registry (issue #11).
//
// Pinned contract: componentRegistry has exactly one entry per COMPONENT_TYPES
// member, keyed in the same fixed order, and getComponent misses closed for
// off-catalog types — the renderer's unknown-type check relies on that miss.

import { describe, expect, it } from "vitest";
import { COMPONENT_TYPES } from "../../lib/a2ui";
import { componentRegistry, getComponent, makePlaceholder } from "./registry";

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

  it("makePlaceholder produces distinct components per type", () => {
    expect(makePlaceholder("kpi_row")).not.toBe(makePlaceholder("insight_text"));
  });
});

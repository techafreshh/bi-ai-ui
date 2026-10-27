// Issue #13: consume the streamed a2ui layout on the canvas. Mirrors the
// proven useDecisions subscribe loop: useAgent({agentId}), re-subscribe on
// agent identity change with history reset, return unsubscribe. The phase
// machine drives skeleton-then-fill: RUN_STARTED fires after one localhost
// POST round-trip (tens of ms, far inside the 400ms skeleton bound) and
// precedes the first read_state snapshot; a snapshot carrying a plain-object
// a2ui advances to layout (filled once slotCopy holds ≥1 key); a run that
// ends with no layout falls back to idle. An unparseable-but-object a2ui
// still advances — the canvas degrades to the renderer's fallback card, so
// a garbage layout can never wedge the canvas on a skeleton.

import { useEffect, useRef, useState } from "react";
import { useAgent } from "@copilotkit/react-core/v2";

export type A2UIPhase = "idle" | "pending" | "layout" | "filled";

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// The streamed narrative the SlotCopyProvider reads: the layout envelope's
// own slotCopy key only (Object.hasOwn — the same prototype-chain hazard
// lib/decisions.ts guards), string values only. A non-object envelope, a
// missing key or a non-object slotCopy all read as {} — never throw.
export function slotCopyOf(layout: unknown): Record<string, string> {
  if (!isPlainObject(layout)) return {};
  if (!Object.hasOwn(layout, "slotCopy")) return {};
  const raw = layout.slotCopy;
  if (!isPlainObject(raw)) return {};
  const copy: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === "string") copy[key] = value;
  }
  return copy;
}

export function useA2UI(agentId: string): {
  a2ui: unknown | null;
  phase: A2UIPhase;
} {
  const { agent } = useAgent({ agentId });
  const [a2ui, setA2ui] = useState<unknown | null>(null);
  const [phase, setPhase] = useState<A2UIPhase>("idle");
  // The end-of-run callbacks cannot read the state setter's latest value,
  // so the ref mirrors it: a run that ends while a layout is held keeps the
  // canvas; one that ends layout-less falls back to idle.
  const a2uiRef = useRef<unknown | null>(null);

  useEffect(() => {
    // Fresh agent reference (the runtime sync swaps provisional stand-ins
    // in) starts its own broadcast history: pending/layout from a previous
    // instance must not leak into the new subscription.
    a2uiRef.current = null;
    setA2ui(null);
    setPhase("idle");
    // Late subscribers (chat bubbles mount mid-run, on the first text
    // chunk — after the layout snapshots already fired) sync from the
    // agent's CURRENT state: missed events never replay, so without this
    // the bubble would idle forever while the canvas shows filled.
    const current = (agent as { state?: unknown }).state;
    if (
      isPlainObject(current) &&
      Object.hasOwn(current, "a2ui") &&
      isPlainObject(current.a2ui)
    ) {
      a2uiRef.current = current.a2ui;
      setA2ui(current.a2ui);
      setPhase(
        Object.keys(slotCopyOf(current.a2ui)).length > 0 ? "filled" : "layout",
      );
    }
    const { unsubscribe } = agent.subscribe({
      onRunStartedEvent: () => {
        // A new run supersedes whatever the canvas holds — even a filled
        // layout from the previous turn yields to the skeleton grid.
        a2uiRef.current = null;
        setA2ui(null);
        setPhase("pending");
      },
      onStateChanged: ({ state }) => {
        if (!isPlainObject(state)) return;
        // Own-property-only lookup (same hazard as the component registry):
        // an inherited `a2ui` off Object.prototype must not advance the
        // phase or overwrite the canvas.
        if (!Object.hasOwn(state, "a2ui")) return;
        const layout = state.a2ui;
        // Missing/null/scalar layouts are ignored — the skeleton holds
        // until a real envelope arrives. A plain object that fails Zod
        // still advances: the renderer degrades it to the fallback card.
        if (!isPlainObject(layout)) return;
        a2uiRef.current = layout;
        setA2ui(layout);
        setPhase(
          Object.keys(slotCopyOf(layout)).length > 0 ? "filled" : "layout",
        );
      },
      onRunFinishedEvent: () => {
        if (a2uiRef.current === null) setPhase("idle");
      },
      onRunFinalized: () => {
        if (a2uiRef.current === null) setPhase("idle");
      },
      onRunFailed: () => {
        if (a2uiRef.current === null) setPhase("idle");
      },
      onRunErrorEvent: () => {
        if (a2uiRef.current === null) setPhase("idle");
      },
    });
    return unsubscribe;
  }, [agent]);

  return { a2ui, phase };
}

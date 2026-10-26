// Issue #17: feed the dashboard canvas from the shared `a2ui` layout the
// agent streams in its state. Mirrors useDecisions' subscription idiom verbatim:
// the adapter snapshots shared state on state-carrying events throughout a run
// (not only at a node's exit), so re-broadcasts of the byte-identical layout are
// skipped via the last-seen ref, while every genuinely new layout REPLACES the
// previous one — the latest run's canvas wins (unlike decisions' union).

import { useEffect, useRef, useState } from "react";
import { useAgent } from "@copilotkit/react-core/v2";
import { a2uiFromState, type A2UILayout } from "../lib/a2ui";

export function useA2UI(agentId: string): A2UILayout | null {
  const { agent } = useAgent({ agentId });
  const [layout, setLayout] = useState<A2UILayout | null>(null);
  const lastSeen = useRef<A2UILayout | null>(null);

  useEffect(() => {
    // Fresh agent reference (the runtime sync swaps provisional stand-ins in)
    // starts its own broadcast history: its first snapshot must not be
    // deduped against a previous agent instance's last-seen layout.
    lastSeen.current = null;
    const { unsubscribe } = agent.subscribe({
      onStateChanged: ({ state }) => {
        // Invalid or absent payloads never clear the canvas mid-run — the
        // previous layout stays until a valid new one replaces it.
        const incoming = a2uiFromState(state);
        if (!incoming) return;
        // Latest-wins with byte-identical skip (sameDecisions' JSON-text
        // idiom): the adapter re-broadcasts the same snapshot many times
        // per run, and only a genuinely different layout is new state.
        if (JSON.stringify(incoming) === JSON.stringify(lastSeen.current)) {
          return;
        }
        lastSeen.current = incoming;
        setLayout(incoming);
      },
    });
    return unsubscribe;
  }, [agent]);

  return layout;
}

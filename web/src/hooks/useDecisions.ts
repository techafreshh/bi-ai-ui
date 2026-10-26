// Issue #14: feed the decision log from the shared `decisions[]` the agent
// streams in its state. Subscribes to the AG-UI agent via CopilotKit v2's
// useAgent and accumulates a UNION of every distinct decisions array seen:
// each run's decide exit broadcasts a fresh array (latency floats differ per
// run), so two queries accumulate both runs' entries and the drawer's count
// equals the sum of both decisions[] lengths. Re-broadcasts of an identical
// array (the adapter snapshots state on state-carrying events throughout a
// run, not only at decide's exit) are skipped via sameDecisions.

import { useEffect, useRef, useState } from "react";
import { useAgent } from "@copilotkit/react-core/v2";
import {
  decisionsFromState,
  sameDecisions,
  type DecisionRecord,
} from "../lib/decisions";

export function useDecisions(agentId: string): DecisionRecord[] {
  const { agent } = useAgent({ agentId });
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const lastSeen = useRef<DecisionRecord[] | null>(null);

  useEffect(() => {
    // Fresh agent reference (the runtime sync swaps provisional stand-ins in)
    // starts its own broadcast history: its first snapshot must not be
    // deduped against a previous agent instance's last-seen array.
    lastSeen.current = null;
    const { unsubscribe } = agent.subscribe({
      onStateChanged: ({ state }) => {
        const incoming = decisionsFromState(state);
        if (
          incoming.length === 0 ||
          sameDecisions(incoming, lastSeen.current ?? [])
        ) {
          return;
        }
        lastSeen.current = incoming;
        // Union semantics: append, never replace — duplicates across runs
        // are preserved (ids recur every run).
        setDecisions((prev) => [...prev, ...incoming]);
      },
    });
    return unsubscribe;
  }, [agent]);

  return decisions;
}

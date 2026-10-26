// Issue #16: the Investigate CTA's send-side hook — the twin of
// useDecisions. The variance_callout's `cta` slot carries the agent's
// machine id (agent/graph.py's INVESTIGATE_CTA, "investigate_churn_cohort");
// pressing the button appends the fixed-prefix follow-up message to the
// SHARED agent thread and runs the agent again, producing a drill-down a2ui.
// The prefix must match agent/graph.py's INVESTIGATE_PREFIX byte-for-byte:
// the graph's read_state routes any message starting with it into the
// cohort drill-down layout.
//
// The React context itself lives in investigateContext.ts (no CopilotKit
// import there) so catalog components can read the handler without loading
// the CopilotKit runtime; this module wires that context to the shared
// agent below CopilotKitProvider.

import { useCallback, type ReactNode } from "react";
import { useAgent } from "@copilotkit/react-core/v2";
import { InvestigateContext, type InvestigateHandler } from "./investigateContext";

// Verbatim twin of agent/graph.py's INVESTIGATE_PREFIX (issue #16 "Follow-up
// framing: fixed prefix message '[investigate] churn cohort …'").
export const INVESTIGATE_PREFIX = "[investigate] churn cohort";

// Structural subset of the AG-UI message type this hook actually reads:
// the agent's shared `messages` array carries richer shapes (content parts),
// all of which degrade to "no quoted text" here.
type AgentMessageLike = { role?: string; content?: unknown };

/**
 * The framed follow-up message for one CTA press: the fixed prefix, an
 * em-dash, then the last user message's text — quoting the user's question
 * is what lets the agent re-resolve the display window the callout came
 * from ("… — Why did churn spike in August?" re-pins August). No user
 * message with non-empty string content (including the first turn and
 * part-list contents) reads as the bare prefix.
 */
export function buildInvestigateMessage(
  messages: readonly AgentMessageLike[] | undefined,
): string {
  let quoted: string | null = null;
  for (const message of messages ?? []) {
    if (message.role !== "user" || typeof message.content !== "string") continue;
    const text = message.content.trim();
    if (text) quoted = text;
  }
  return quoted ? `${INVESTIGATE_PREFIX} — ${quoted}` : INVESTIGATE_PREFIX;
}

// Message ids are client-authored: crypto.randomUUID when the runtime has
// one, a monotonic fallback otherwise (no reliance on CopilotKit internals).
function newMessageId(): string {
  const id = typeof crypto !== "undefined" ? crypto.randomUUID?.() : undefined;
  return id ?? `cta-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Mounts the Investigate send path below CopilotKitProvider: `useAgent`
 * resolves the same self-managed agent instance the sidebar renders from,
 * so the CTA's messages land on the visible thread. Pressing Investigate
 * appends the framed follow-up and re-runs the agent — literally the
 * sidebar's own send path (addMessage + runAgent), no extra transport.
 *
 * While a run is in flight (or before the agent resolves) the handler
 * declines loudly instead of queueing a second concurrent run.
 */
export function InvestigateProvider({
  agentId,
  children,
}: {
  agentId: string;
  children: ReactNode;
}) {
  const { agent } = useAgent({ agentId });

  const investigate = useCallback<InvestigateHandler>(
    (cta) => {
      if (!agent || agent.isRunning) {
        console.info(
          `[a2ui] investigate skipped (${agent ? "agent busy" : "agent unavailable"}): ${cta}`,
        );
        return;
      }
      const content = buildInvestigateMessage(agent.messages);
      console.info(`[a2ui] investigate: ${cta} — ${content}`);
      agent.addMessage({ id: newMessageId(), role: "user", content });
      agent.runAgent().catch((error: unknown) => {
        // The thread stays intact: the user can re-press or ask manually.
        console.error(`[a2ui] investigate run failed: ${String(error)}`);
      });
    },
    [agent],
  );

  return (
    <InvestigateContext.Provider value={investigate}>
      {children}
    </InvestigateContext.Provider>
  );
}

export { useInvestigate } from "./investigateContext";

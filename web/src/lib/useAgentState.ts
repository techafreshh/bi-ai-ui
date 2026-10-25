/**
 * Subscribe the canvas to the bi_agent's state triple.
 *
 * The v2 hook surface (verified against the pinned @copilotkit/react-core
 * 1.79.0 typings): useAgent({ agentId }) returns the shared registry agent;
 * agent.subscribe({ onStateChanged }) fires on every applied state mutation
 * (STATE_SNAPSHOT / STATE_DELTA), and agent.state holds the merged values.
 * The send path is agent.addMessage(...) + agent.runAgent() — same instance
 * the CopilotSidebar chat drives, so CTA runs share the chat thread.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { AbstractAgent } from "@ag-ui/client";
import { useAgent } from "@copilotkit/react-core/v2";
import { extractAgentUiState, type AgentUiState } from "./a2ui";

// Must match LangGraphAGUIAgent(name=...) in agent/server.py (App.tsx owns
// the same constant — re-declared here to keep this module import-free of
// the app shell).
export const AGENT_ID = "bi_agent";

const EMPTY: AgentUiState = { a2ui: null, decisions: [], status: {} };

export function useAgentState(): {
  state: AgentUiState;
  agent: AbstractAgent | null;
  isReady: boolean;
  send: (text: string) => Promise<void>;
  isRunning: boolean;
} {
  const { agent, isReady } = useAgent({ agentId: AGENT_ID });
  const [state, setState] = useState<AgentUiState>(EMPTY);
  const [isRunning, setIsRunning] = useState(false);
  const agentRef = useRef<AbstractAgent | null>(null);
  agentRef.current = agent ?? null;

  useEffect(() => {
    if (!agent) return;
    // Seed from whatever state the agent already carries (mid-run joins).
    setState(extractAgentUiState(agent.state));
    const subscription = agent.subscribe({
      onStateChanged: () => setState(extractAgentUiState(agent.state)),
    });
    return () => subscription.unsubscribe();
  }, [agent]);

  useEffect(() => {
    if (!agent) return;
    const subscription = agent.subscribe({
      onRunStartedEvent: () => {
        setIsRunning(true);
      },
      onRunFinalized: () => {
        setIsRunning(false);
      },
      onRunFailed: () => {
        setIsRunning(false);
      },
    });
    return () => subscription.unsubscribe();
  }, [agent]);

  const send = useCallback(async (text: string) => {
    const current = agentRef.current;
    if (!current || current.isRunning) return;
    current.addMessage({ id: crypto.randomUUID(), role: "user", content: text });
    await current.runAgent();
  }, []);

  return { state, agent, isReady, send, isRunning };
}

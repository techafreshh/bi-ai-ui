import { HttpAgent } from "@ag-ui/client";
import { useState } from "react";
import { CopilotKitProvider, CopilotSidebar } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";

import Canvas from "./components/Canvas";
import DecisionDrawer from "./components/DecisionDrawer";
import Suggestions from "./components/Suggestions";
import { AGENT_ID, useAgentState } from "./lib/useAgentState";

// AG-UI endpoint registered by agent/server.py on POST /copilotkit (:8123
// pinned per the CopilotKit LangGraph quickstart).
const AGENT_URL = "http://localhost:8123/copilotkit";

// Direct browser -> FastAPI agent connection (no CopilotKit runtime proxy in
// between): HttpAgent POSTs RunAgentInput and consumes the AG-UI SSE stream.
const biAgent = new HttpAgent({
  agentId: AGENT_ID,
  url: AGENT_URL,
  description: "bi-ai-ui agent: composes dashboard views from a fixed catalog.",
});

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { state, send, isRunning } = useAgentState();

  const sendIfIdle = (text: string) => {
    if (!isRunning) {
      void send(text);
    }
  };

  return (
    <CopilotKitProvider selfManagedAgents={{ [AGENT_ID]: biAgent }}>
      <Suggestions />
      <div className="flex h-full flex-col bg-slate-50 text-slate-900">
        <header className="flex items-center gap-4 border-b border-slate-200 bg-white px-6 py-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">bi-ai-ui</h1>
            <p className="text-xs text-slate-500">
              Generative BI — the agent chooses components, never invents markup
            </p>
          </div>
          <div className="ml-auto flex items-center gap-3">
            {state.status.mode ? (
              <span
                className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                  state.status.mode === "demo-local"
                    ? "border-slate-200 bg-slate-100 text-slate-600"
                    : "border-emerald-200 bg-emerald-50 text-emerald-700"
                }`}
              >
                {state.status.mode === "demo-local" ? "demo mode (local policy)" : "live decisions"}
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
            >
              Decision log
            </button>
          </div>
        </header>

        <main className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1">
            <Canvas
              a2ui={state.a2ui}
              decisions={state.decisions}
              status={state.status}
              onAction={sendIfIdle}
            />
          </div>
          <CopilotSidebar
            agentId={AGENT_ID}
            defaultOpen
            labels={{ chatInputPlaceholder: "Type a message…" }}
          />
        </main>

        <DecisionDrawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          decisions={state.decisions}
        />
      </div>
    </CopilotKitProvider>
  );
}

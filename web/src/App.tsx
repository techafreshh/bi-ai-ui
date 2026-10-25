import { HttpAgent } from "@ag-ui/client";
import { CopilotKitProvider, CopilotSidebar } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";

// AG-UI endpoint registered by agent/server.py on POST /copilotkit (:8123
// pinned per the CopilotKit LangGraph quickstart).
const AGENT_URL = "http://localhost:8123/copilotkit";

// Must match LangGraphAGUIAgent(name=...) in agent/server.py.
const AGENT_ID = "bi_agent";

// Direct browser -> FastAPI agent connection (no CopilotKit runtime proxy in
// between): HttpAgent POSTs RunAgentInput and consumes the AG-UI SSE stream.
const biAgent = new HttpAgent({
  agentId: AGENT_ID,
  url: AGENT_URL,
  description: "bi-ai-ui chat agent: answers business questions in plain text.",
});

export default function App() {
  return (
    <CopilotKitProvider selfManagedAgents={{ [AGENT_ID]: biAgent }}>
      <div className="flex h-full flex-col bg-slate-50 text-slate-900">
        <header className="border-b border-slate-200 bg-white px-6 py-4">
          <h1 className="text-lg font-semibold tracking-tight">bi-ai-ui</h1>
          <p className="text-sm text-slate-500">
            AI BI dashboard — walking skeleton
          </p>
        </header>
        <main className="flex flex-1 items-center justify-center px-6">
          <p className="max-w-md text-center text-sm text-slate-500">
            Dashboard canvas (later phases render components here). Send
            &ldquo;ping&rdquo; in the sidebar and the agent&rsquo;s streamed
            reply appears in the message list.
          </p>
        </main>
        <CopilotSidebar
          agentId={AGENT_ID}
          defaultOpen
          labels={{ chatInputPlaceholder: "Type a message…" }}
        />
      </div>
    </CopilotKitProvider>
  );
}

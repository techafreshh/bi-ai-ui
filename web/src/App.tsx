import { CopilotKitProvider, CopilotSidebar } from "@copilotkit/react-core/v2";
import { HttpAgent } from "@ag-ui/client";

// The agent's CopilotKit/AG-UI endpoint (PRD §10: port 8123 pinned per quickstart).
const AGENT_URL = "http://localhost:8123/copilotkit";

// Direct browser -> FastAPI connection: HttpAgent POSTs AG-UI runs straight to
// the agent's /copilotkit endpoint (no Node CopilotKit runtime in between).
const agent = new HttpAgent({ agentId: "bi_agent", url: AGENT_URL });

export default function App() {
  return (
    <CopilotKitProvider selfManagedAgents={{ bi_agent: agent }}>
      <div className="flex h-full min-h-screen flex-col">
        <header className="border-b border-slate-200 bg-white px-6 py-4">
          <h1 className="text-lg font-semibold tracking-tight">bi-ai-ui</h1>
          <p className="text-sm text-slate-500">
            AI BI dashboard — walking skeleton
          </p>
        </header>
        <main className="flex flex-1 items-center justify-center p-8">
          <div className="max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center shadow-sm">
            <p className="text-sm text-slate-600">
              The workspace canvas lands here. Open the chat sidebar and send a
              message — replies stream from the LangGraph agent.
            </p>
          </div>
        </main>
        <CopilotSidebar
          agentId="bi_agent"
          defaultOpen
          labels={{ chatInputPlaceholder: "Type a message…" }}
        />
      </div>
    </CopilotKitProvider>
  );
}

import { CopilotKitProvider, CopilotSidebar } from "@copilotkit/react-core/v2";
import { HttpAgent } from "@ag-ui/client";
import "@copilotkit/react-core/v2/styles.css";

// PRD v1.1: the provider talks to the agent's FastAPI endpoint directly —
// browser → AG-UI SSE on :8123, no Node runtime in between. This requires the
// agent's CORS config to allow http://localhost:5173.
const AGENT_URL = "http://localhost:8123/copilotkit";

// Must match the LangGraphAGUIAgent name in agent/server.py.
const AGENT_ID = "bi_agent";

const biAgent = new HttpAgent({ agentId: AGENT_ID, url: AGENT_URL });

export default function App() {
  return (
    <CopilotKitProvider selfManagedAgents={{ [AGENT_ID]: biAgent }}>
      <div className="flex h-full flex-col bg-slate-50 text-slate-900">
        <header className="border-b border-slate-200 bg-white px-6 py-4">
          <h1 className="text-lg font-semibold">bi-ai-ui</h1>
          <p className="text-sm text-slate-500">
            Walking skeleton — chat round-trip to the LangGraph agent
          </p>
        </header>
        <main className="flex flex-1 items-center justify-center p-8">
          <div className="max-w-md rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
            <h2 className="mb-2 text-base font-medium">Dashboard canvas</h2>
            <p className="text-sm text-slate-500">
              Components will assemble here in later phases. Use the sidebar to
              chat with the agent — try sending &ldquo;ping&rdquo;.
            </p>
          </div>
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

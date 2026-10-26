import { CopilotKitProvider, CopilotSidebar } from "@copilotkit/react-core/v2";
import { HttpAgent } from "@ag-ui/client";
import "@copilotkit/react-core/v2/styles.css";
import { DashboardCanvas } from "./components/DashboardCanvas";
import { DecisionDrawer } from "./components/DecisionDrawer";
import { ThemeToggle } from "./components/ui/theme-toggle";
import { useDecisions } from "./hooks/useDecisions";
import { InvestigateProvider } from "./hooks/useInvestigate";
import { ThemeProvider } from "./lib/theme";

// PRD v1.1: the provider talks to the agent's FastAPI endpoint directly —
// browser → AG-UI SSE on :8123, no Node runtime in between. This requires the
// agent's CORS config to allow http://localhost:5173.
const AGENT_URL = "http://localhost:8123/copilotkit";

// Must match the LangGraphAGUIAgent name in agent/server.py.
const AGENT_ID = "bi_agent";

const biAgent = new HttpAgent({ agentId: AGENT_ID, url: AGENT_URL });

// Issue #14: useAgent (like any context hook) only resolves below the
// provider, so the provider renders this shell instead of inlining it — the
// decision log accumulates here and the drawer mounts at the header's right.
function AppShell() {
  const decisions = useDecisions(AGENT_ID);

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <header className="flex items-center justify-between border-b border-border bg-card px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">bi-ai-ui</h1>
          <p className="text-sm text-muted-foreground">
            Demo canvas — six catalog components on beUI cards, fixture-driven
            until the agent streams a layout
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DecisionDrawer decisions={decisions} />
          <ThemeToggle />
        </div>
      </header>
      {/* Issue #16: the Investigate CTA below appends+[re]runs the agent on
          this provider's shared agent instance — the same instance the
          sidebar chats on, so the drill-down lands on the visible thread. */}
      <InvestigateProvider agentId={AGENT_ID}>
        <main className="flex-1 p-8">
          <DashboardCanvas />
        </main>
      </InvestigateProvider>
      <CopilotSidebar
        agentId={AGENT_ID}
        defaultOpen
        labels={{ chatInputPlaceholder: "Type a message…" }}
      />
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <CopilotKitProvider selfManagedAgents={{ [AGENT_ID]: biAgent }}>
        <AppShell />
      </CopilotKitProvider>
    </ThemeProvider>
  );
}

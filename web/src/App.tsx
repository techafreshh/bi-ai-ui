import { CopilotKitProvider } from "@copilotkit/react-core/v2";
import { HttpAgent } from "@ag-ui/client";
import "@copilotkit/react-core/v2/styles.css";
import { DashboardCanvas } from "./components/DashboardCanvas";
import { DecisionDrawer } from "./components/DecisionDrawer";
import { ThemeToggle } from "./components/ui/theme-toggle";
import { ChatSidebar } from "./components/ui/chat/ChatSidebar";
import { useDecisions } from "./hooks/useDecisions";
import { InvestigateProvider } from "./hooks/useInvestigate";
import { ThemeProvider } from "./lib/theme";
import { AGENT_ID, AGENT_URL } from "./lib/agent";

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
      <ChatSidebar />
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

// Issue #13: the one shared agent identity — PRD v1.1: the provider talks
// to the agent's FastAPI endpoint directly (browser → AG-UI SSE on :8123,
// no Node runtime in between). Moved verbatim from App.tsx so App, the chat
// sidebar and the stream hooks share a single source (and break the
// App→ChatSidebar→App import cycle).

// PRD v1.1: the provider talks to the agent's FastAPI endpoint directly —
// browser → AG-UI SSE on :8123, no Node runtime in between. This requires the
// agent's CORS config to allow the SPA's origin (agent/server.py:SPA_ORIGIN).
//
// VITE_AGENT_URL overrides the browser-visible endpoint (Docker port remaps,
// remote deployments) without code edits; Vite inlines it at dev-server
// startup, so an edit applies on container recreate. Empty/whitespace falls
// back to the pinned native-dev URL.
const DEFAULT_AGENT_URL = "http://localhost:8123/copilotkit";

export const AGENT_URL: string =
  (import.meta.env.VITE_AGENT_URL as string | undefined)?.trim() ||
  DEFAULT_AGENT_URL;

// Must match the LangGraphAGUIAgent name in agent/server.py.
export const AGENT_ID = "bi_agent";

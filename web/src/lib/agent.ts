// Issue #13: the one shared agent identity — PRD v1.1: the provider talks
// to the agent's FastAPI endpoint directly (browser → AG-UI SSE on :8123,
// no Node runtime in between). Moved verbatim from App.tsx so App, the chat
// sidebar and the stream hooks share a single source (and break the
// App→ChatSidebar→App import cycle).

// PRD v1.1: the provider talks to the agent's FastAPI endpoint directly —
// browser → AG-UI SSE on :8123, no Node runtime in between. This requires the
// agent's CORS config to allow http://localhost:5173.
export const AGENT_URL = "http://localhost:8123/copilotkit";

// Must match the LangGraphAGUIAgent name in agent/server.py.
export const AGENT_ID = "bi_agent";

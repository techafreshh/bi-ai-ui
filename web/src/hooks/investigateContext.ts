// Issue #16: the Investigate CTA's React context, in a module of its own —
// deliberately free of any @copilotkit import. Catalog components
// (VarianceCallout) read the handler from here, so rendering a callout never
// pulls the CopilotKit runtime (whose CSS side-effect import cannot load in
// plain vitest component suites); the CopilotKit-wired provider lives in
// useInvestigate.tsx and provides this context below CopilotKitProvider.

import { createContext, useContext } from "react";

// The handler appended the framed follow-up message and re-runs the agent on
// the shared thread; null when no InvestigateProvider wraps the tree
// (canvas fixtures, bare renders) — callers stay log-only then.
export type InvestigateHandler = (cta: string) => void;

export const InvestigateContext = createContext<InvestigateHandler | null>(null);

/**
 * The Investigate handler from the surrounding InvestigateProvider, or null
 * when the component renders outside one (canvas fixtures, tests).
 */
export function useInvestigate(): InvestigateHandler | null {
  return useContext(InvestigateContext);
}

/**
 * The three golden-path suggestions (PRD §2): configured on the shared agent
 * so the sidebar renders them as one-click prompts before the first message.
 */
import { useConfigureSuggestions } from "@copilotkit/react-core/v2";

const SUGGESTIONS = [
  { title: "Q3 revenue", message: "Show me sales in Q3" },
  { title: "Why churn spiked", message: "Why did churn spike in August?" },
  { title: "Is the data clean?", message: "Is the data clean?" },
];

export default function Suggestions() {
  useConfigureSuggestions(
    {
      suggestions: SUGGESTIONS,
      available: "before-first-message",
      consumerAgentId: "bi_agent",
    },
    [],
  );
  return null;
}

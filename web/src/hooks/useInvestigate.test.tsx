// Issue #16: the Investigate CTA's send-side hook — framing contract plus
// provider integration. The framing must stay byte-compatible with the
// agent's read_state probe: any message starting with the fixed prefix
// (agent/graph.py's INVESTIGATE_PREFIX) is routed into the churn cohort
// drill-down. The provider tests swap a stub shared agent (@copilotkit v2's
// useAgent is mocked at the top of this file) — the contract under test is
// addMessage + runAgent on that instance, nothing more.

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  INVESTIGATE_PREFIX,
  InvestigateProvider,
  buildInvestigateMessage,
  useInvestigate,
} from "./useInvestigate";

// Hoisted before the component imports, so every consumer of the CopilotKit
// v2 module in this file (the hook included) resolves this stub — one module
// instance, one shared React context. Per-test swap via setAgent.
let agent: unknown;
vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent }),
}));

function setAgent(next: unknown) {
  agent = next;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setAgent(null);
});

describe("buildInvestigateMessage", () => {
  // Local message shape for framing tests (id included, like real AG-UI
  // messages); an annotated variable avoids excess-property checks.
  type Turn = { id: string; role?: string; content?: unknown };
  const turn = (id: string, role: string, content: unknown): Turn => ({
    id,
    role,
    content,
  });

  it("frames the last user message with the fixed agent prefix", () => {
    expect(
      buildInvestigateMessage([
        turn("1", "user", "Why did churn spike in August?"),
        turn("2", "assistant", "Growth churned 40 accounts."),
      ]),
    ).toBe(`${INVESTIGATE_PREFIX} — Why did churn spike in August?`);
  });

  it("skips empty/whitespace and part-list contents, picking the last real user text", () => {
    expect(
      buildInvestigateMessage([
        turn("1", "user", "   "),
        turn("2", "assistant", [{ type: "text", text: "parts are not user speech" }]),
        turn("3", "user", "Why did churn spike in August?"),
        turn("4", "assistant", "The cohort was Growth-only."),
      ]),
    ).toBe(`${INVESTIGATE_PREFIX} — Why did churn spike in August?`);
  });

  it("falls back to the bare prefix without any quotable user message", () => {
    expect(buildInvestigateMessage(undefined)).toBe(INVESTIGATE_PREFIX);
    expect(buildInvestigateMessage([])).toBe(INVESTIGATE_PREFIX);
    expect(
      buildInvestigateMessage([turn("1", "assistant", "no quote")]),
    ).toBe(INVESTIGATE_PREFIX);
  });
});

describe("InvestigateProvider", () => {
  const USER_TURN = {
    id: "m1",
    role: "user",
    content: "Why did churn spike in August?",
  };

  // The consumer forwards its resolved handler out through a prop (a
  // parameter, not a captured let): TypeScript otherwise narrows the
  // captured variable to null and refuses the later call.
  function PressConsumer({
    onHandler,
  }: {
    onHandler: (handler: (cta: string) => void) => void;
  }) {
    const investigate = useInvestigate();
    return (
      <button
        type="button"
        data-testid="press"
        onClick={() => {
          if (investigate) onHandler(investigate);
        }}
      >
        press
      </button>
    );
  }

  async function clickPress() {
    const user = userEvent.setup();
    await user.click(screen.getByTestId("press"));
  }

  it("sends the framed follow-up and re-runs the shared agent", async () => {
    const addMessage = vi.fn();
    const runAgent = vi.fn(() => Promise.resolve({}));
    setAgent({ messages: [USER_TURN], isRunning: false, addMessage, runAgent });
    const handlers: ((cta: string) => void)[] = [];
    render(
      <InvestigateProvider agentId="bi_agent">
        <PressConsumer onHandler={(handler) => handlers.push(handler)} />
      </InvestigateProvider>,
    );
    await clickPress();
    expect(handlers).toHaveLength(1);
    handlers[0]("investigate_churn_cohort");
    expect(addMessage).toHaveBeenCalledTimes(1);
    const message = addMessage.mock.calls[0][0] as {
      id: string;
      role: string;
      content: string;
    };
    expect(message.role).toBe("user");
    expect(message.content).toBe(
      `${INVESTIGATE_PREFIX} — Why did churn spike in August?`,
    );
    expect(typeof message.id).toBe("string");
    expect(message.id.length).toBeGreaterThan(0);
    expect(runAgent).toHaveBeenCalledTimes(1);
  });

  it("declines while the agent is running and when no agent resolved", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const addMessage = vi.fn();
    const runAgent = vi.fn(() => Promise.resolve({}));

    setAgent({ messages: [USER_TURN], isRunning: true, addMessage, runAgent });
    render(
      <InvestigateProvider agentId="bi_agent">
        <PressConsumer onHandler={(handler) => handler("investigate_churn_cohort")} />
      </InvestigateProvider>,
    );
    await clickPress();

    cleanup();
    setAgent(null);
    render(
      <InvestigateProvider agentId="bi_agent">
        <PressConsumer onHandler={(handler) => handler("investigate_churn_cohort")} />
      </InvestigateProvider>,
    );
    await clickPress();

    expect(addMessage).not.toHaveBeenCalled();
    expect(runAgent).not.toHaveBeenCalled();
    expect(info).toHaveBeenCalledTimes(2);
  });

  it("keeps the thread alive when the run rejects", async () => {
    const logError = vi.spyOn(console, "error").mockImplementation(() => {});
    const runAgent = vi.fn(() => Promise.reject(new Error("network down")));
    setAgent({ messages: [USER_TURN], isRunning: false, addMessage: vi.fn(), runAgent });
    const handlers: ((cta: string) => void)[] = [];
    render(
      <InvestigateProvider agentId="bi_agent">
        <PressConsumer onHandler={(handler) => handlers.push(handler)} />
      </InvestigateProvider>,
    );
    await clickPress();
    handlers[0]("investigate_churn_cohort");
    // The rejection is async; flush microtasks so the .catch path runs.
    await act(async () => {});
    expect(runAgent).toHaveBeenCalledTimes(1);
    expect(logError).toHaveBeenCalledWith(
      expect.stringContaining("investigate run failed"),
    );
    expect(logError).toHaveBeenCalledWith(
      expect.stringContaining("network down"),
    );
  });
});

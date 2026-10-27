// Issue #13: scripted AbstractAgent double for the stream-hook and canvas
// probes. Implements only the surface the hooks touch — subscribe() with
// the AG-UI subscriber callbacks they register, plus the addMessage/runAgent
// send path the Investigate CTA needs. Emitters invoke the CURRENT
// subscriber synchronously, so with fake timers a runStarted emit lands in
// the same commit — the ≤400ms skeleton evidence. The module singleton (via
// setFakeAgent/getFakeAgent) lets a vi.mock factory for
// "@copilotkit/react-core/v2" resolve the active double without touching
// the 2MB CopilotKit runtime; the agent-swap test replaces the singleton to
// prove the useDecisions-style re-subscribe lifecycle.

export interface FakeSubscriber {
  onStateChanged?: (params: { state: unknown }) => unknown;
  onRunStartedEvent?: (params?: unknown) => unknown;
  onRunFinishedEvent?: (params?: unknown) => unknown;
  onRunFinalized?: (params?: unknown) => unknown;
  onRunFailed?: (params?: unknown) => unknown;
  onRunErrorEvent?: (params?: unknown) => unknown;
}

export class FakeAgent {
  subscriber: FakeSubscriber | null = null;
  subscribeCalls = 0;
  unsubscribeCalls = 0;
  messages: Array<{ role?: string; content?: unknown }> = [];
  // Mirrors AbstractAgent.state: the accumulated snapshot late subscribers
  // sync from on (re)subscribe. emitState merges like the real client.
  state: Record<string, unknown> = {};
  isRunning = false;

  subscribe(subscriber: FakeSubscriber): { unsubscribe: () => void } {
    this.subscriber = subscriber;
    this.subscribeCalls += 1;
    return {
      unsubscribe: () => {
        this.unsubscribeCalls += 1;
        if (this.subscriber === subscriber) this.subscriber = null;
      },
    };
  }

  addMessage(message: { role?: string; content?: unknown }): void {
    this.messages.push(message);
  }

  async runAgent(): Promise<Record<string, never>> {
    return {};
  }

  emitRunStarted(): void {
    this.subscriber?.onRunStartedEvent?.({});
  }

  emitState(state: unknown): void {
    if (
      typeof state === "object" &&
      state !== null &&
      !Array.isArray(state)
    ) {
      this.state = { ...this.state, ...state };
    }
    this.subscriber?.onStateChanged?.({ state });
  }

  emitRunFinished(): void {
    this.subscriber?.onRunFinishedEvent?.({});
  }

  emitRunFinalized(): void {
    this.subscriber?.onRunFinalized?.({});
  }

  emitRunFailed(): void {
    this.subscriber?.onRunFailed?.({});
  }

  emitRunError(): void {
    this.subscriber?.onRunErrorEvent?.({});
  }
}

let active: FakeAgent = new FakeAgent();

export function setFakeAgent(next: FakeAgent): void {
  active = next;
}

export function getFakeAgent(): FakeAgent {
  return active;
}

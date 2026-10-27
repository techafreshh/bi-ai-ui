// Issue #13: the useA2UI phase machine, proved against the scripted
// FakeAgent with fake timers — the ≤400ms skeleton clause is evidence, not
// narration: runStarted lands pending in the same commit and holds at
// +400ms. The whole-module mock keeps the 2MB CopilotKit runtime out of the
// bundle; the hook only ever touches useAgent, so exporting just that seam
// is faithful.

import { cleanup, renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useA2UI, slotCopyOf } from "./useA2UI";
import {
  FakeAgent,
  getFakeAgent,
  setFakeAgent,
} from "../test/fakeAgent";

vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent: getFakeAgent(), isReady: true }),
}));

beforeEach(() => {
  vi.useFakeTimers();
  setFakeAgent(new FakeAgent());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const LAYOUT_ONLY = {
  v: 1,
  layout: "grid",
  items: [
    {
      type: "kpi_row",
      props: { kpis: [{ label: "Net-new ARR (x)", value: 1, delta: 0.1 }] },
    },
  ],
};

const LAYOUT_FILLED = {
  ...LAYOUT_ONLY,
  slotCopy: { "line_chart.title": "Daily ARR through Q3" },
};

describe("useA2UI (acceptance 13: skeleton-then-fill)", () => {
  it("starts idle with no layout", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    expect(result.current.phase).toBe("idle");
    expect(result.current.a2ui).toBeNull();
  });

  it("send→pending in the same tick and still pending at +400ms", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    expect(result.current.phase).toBe("pending");
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(result.current.phase).toBe("pending");
  });

  it("pending→layout on a layout-only snapshot", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitState({ a2ui: LAYOUT_ONLY });
    });
    expect(result.current.phase).toBe("layout");
    expect(result.current.a2ui).toEqual(LAYOUT_ONLY);
  });

  it("layout→filled when slotCopy gains a key", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitState({ a2ui: LAYOUT_ONLY });
    });
    expect(result.current.phase).toBe("layout");
    act(() => {
      getFakeAgent().emitState({ a2ui: LAYOUT_FILLED });
    });
    expect(result.current.phase).toBe("filled");
    expect(result.current.a2ui).toEqual(LAYOUT_FILLED);
  });

  it("fills directly when the first snapshot already carries slotCopy", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitState({ a2ui: LAYOUT_FILLED });
    });
    expect(result.current.phase).toBe("filled");
  });

  it("ignores a non-object a2ui and holds the skeleton", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitState({ a2ui: "garbage-string" });
    });
    expect(result.current.phase).toBe("pending");
    expect(result.current.a2ui).toBeNull();
  });

  it("ignores snapshots with no own a2ui key", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitState({ status: "reading_state" });
    });
    expect(result.current.phase).toBe("pending");
  });

  it("advances on an unparseable-but-object a2ui (renderer degrades it)", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitState({ a2ui: { v: 2 } });
    });
    expect(result.current.phase).toBe("layout");
    expect(result.current.a2ui).toEqual({ v: 2 });
  });

  it("a run ending with no layout falls back to idle", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    expect(result.current.phase).toBe("pending");
    act(() => {
      getFakeAgent().emitRunFinalized();
    });
    expect(result.current.phase).toBe("idle");
  });

  it("a failed run with no layout falls back to idle", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitRunFailed();
    });
    expect(result.current.phase).toBe("idle");
  });

  it("a finished run keeps the layout it streamed", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    act(() => {
      getFakeAgent().emitState({ a2ui: LAYOUT_FILLED });
    });
    act(() => {
      getFakeAgent().emitRunFinished();
      getFakeAgent().emitRunFinalized();
    });
    expect(result.current.phase).toBe("filled");
    expect(result.current.a2ui).toEqual(LAYOUT_FILLED);
  });

  it("a new run supersedes the previous layout with a fresh skeleton", () => {
    const { result } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      getFakeAgent().emitRunStarted();
      getFakeAgent().emitState({ a2ui: LAYOUT_FILLED });
    });
    expect(result.current.phase).toBe("filled");
    act(() => {
      getFakeAgent().emitRunStarted();
    });
    expect(result.current.phase).toBe("pending");
    expect(result.current.a2ui).toBeNull();
  });

  it("a late subscriber syncs the filled layout from current agent state", () => {
    const fake = getFakeAgent();
    fake.emitRunStarted();
    fake.emitState({ a2ui: LAYOUT_FILLED });
    // A bubble mounting now missed every event — it still reads filled.
    const { result } = renderHook(() => useA2UI("bi_agent"));
    expect(result.current.phase).toBe("filled");
    expect(result.current.a2ui).toEqual(LAYOUT_FILLED);
  });

  it("a late subscriber syncs a layout-only snapshot as layout", () => {
    const fake = getFakeAgent();
    fake.emitRunStarted();
    fake.emitState({ a2ui: LAYOUT_ONLY });
    const { result } = renderHook(() => useA2UI("bi_agent"));
    expect(result.current.phase).toBe("layout");
  });

  it("an agent-instance swap re-subscribes and resets history", () => {
    const first = getFakeAgent();
    const { result, rerender } = renderHook(() => useA2UI("bi_agent"));
    act(() => {
      first.emitRunStarted();
      first.emitState({ a2ui: LAYOUT_FILLED });
    });
    expect(result.current.phase).toBe("filled");
    expect(first.subscribeCalls).toBe(1);
    const second = new FakeAgent();
    setFakeAgent(second);
    rerender();
    // The effect tore down the old subscription and opened one on the new
    // identity with a clean slate — the useDecisions lifecycle verbatim.
    expect(first.unsubscribeCalls).toBe(1);
    expect(second.subscribeCalls).toBe(1);
    expect(result.current.phase).toBe("idle");
    expect(result.current.a2ui).toBeNull();
  });
});

describe("slotCopyOf", () => {
  it("reads the envelope's own slotCopy, string values only", () => {
    expect(
      slotCopyOf({ slotCopy: { "line_chart.title": "T", n: 42 } }),
    ).toEqual({ "line_chart.title": "T" });
  });

  it("reads {} for missing, non-object or inherited slotCopy", () => {
    expect(slotCopyOf(null)).toEqual({});
    expect(slotCopyOf("x")).toEqual({});
    expect(slotCopyOf({})).toEqual({});
    expect(slotCopyOf({ slotCopy: "nope" })).toEqual({});
    const inherited = Object.create({ slotCopy: { a: "b" } });
    expect(slotCopyOf(inherited)).toEqual({});
  });
});

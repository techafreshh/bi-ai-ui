// Vitest jsdom polyfills for the catalog components (issue #12).
//
// jsdom implements none of the browser APIs the beUI motion primitives and
// Recharts require, and each fails hard — not gracefully — without it:
// - motion's useReducedMotion calls window.matchMedia at render;
// - useInView constructs an IntersectionObserver and crashes on undefined;
// - Recharts' ResponsiveContainer measures via ResizeObserver.
// The stubs below make every component behave as if visible, full-size and
// reduced-motion, so tests assert the settled final frame synchronously.

class IntersectionObserverStub {
  readonly root: Element | null = null;
  readonly rootMargin: string = "";
  readonly thresholds: ReadonlyArray<number> = [];
  constructor(private readonly callback: IntersectionObserverCallback) {}
  observe(target: Element): void {
    // Report as intersecting SYNCHRONOUSLY: an async (microtask) entry would
    // schedule React state updates outside act(), leaving the first rendered
    // frame stale. Motion's useInView then flips in the same commit and the
    // component settles on its final frame before render() returns.
    this.callback(
      [
        {
          isIntersecting: true,
          target,
          intersectionRatio: 1,
          time: 0,
          boundingClientRect: target.getBoundingClientRect(),
          intersectionRect: target.getBoundingClientRect(),
          rootBounds: null,
        } as unknown as IntersectionObserverEntry,
      ],
      this as unknown as IntersectionObserver,
    );
  }
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

Object.defineProperty(window, "IntersectionObserver", {
  writable: true,
  configurable: true,
  value: IntersectionObserverStub,
});

class ResizeObserverStub {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element): void {
    // Synchronous, as above: Recharts' ResponsiveContainer measures inside
    // its own effect, so the chart renders its 1024x768 layout in the same
    // act flush and the SVG exists before assertions run.
    this.callback(
      [
        {
          target,
          contentRect: {
            width: 1024,
            height: 768,
            x: 0,
            y: 0,
            top: 0,
            left: 0,
            right: 1024,
            bottom: 768,
            toJSON: () => ({}),
          } as DOMRectReadOnly,
          borderBoxSize: [],
          contentBoxSize: [],
          devicePixelContentBoxSize: [],
        } as unknown as ResizeObserverEntry,
      ],
      this as unknown as ResizeObserver,
    );
  }
  unobserve(): void {}
  disconnect(): void {}
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  configurable: true,
  value: ResizeObserverStub,
});

Object.defineProperty(window, "matchMedia", {
  writable: true,
  configurable: true,
  // motion-dom initializes its reduced-motion global with the bare query
  // "(prefers-reduced-motion)" while the CSS idiom is ": reduce"; both must
  // answer true, and the "no-preference" complement must answer false —
  // animations then take their instant reduced path in every test.
  value: (query: string): MediaQueryList =>
    ({
      matches:
        query.includes("prefers-reduced-motion") &&
        !query.includes("no-preference"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
});

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
    // Report as intersecting on the next microtask, matching the async shape
    // of the real observer so effects that await it still settle.
    queueMicrotask(() => {
      // jsdom has no getBoundingClientRect content, so report a visible box.
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
    });
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
    // Fixed 1024x768 content box: chart containers get a deterministic size.
    queueMicrotask(() => {
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
    });
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
  value: (query: string): MediaQueryList =>
    ({
      matches: query.includes("prefers-reduced-motion: reduce"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
});
